"""Rotas de análise patrimonial, quant IA, benchmarks, artefatos, snowball, dividendos, early warning e webhook Telegram."""
import os
import io
import time
import json
import asyncio
import sqlite3
import urllib.request
from datetime import datetime
from typing import Optional, List, Dict, Any
import pandas as pd
from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse, StreamingResponse

from barsi.config import settings
from barsi.infra.logging import logger
from barsi.adapters.factory import AdapterFactory
from barsi.adapters.history.budget_repository import BudgetRepository
from barsi.domain.models import Position
from barsi.domain.rules import atualizar_posicao_quant
from barsi.services.early_warning_service import EarlyWarningService
from barsi.services.snowball_backtest_service import SnowballBacktestService
from barsi.api.common import (
    get_current_user,
    require_role_action,
    get_canonical_positions,
    invalidate_canonical_positions_cache,
    get_sync_service,
    ArtifactCreateRequest,
    FeedbackRequest,
    FeedbackBatchRequest,
    FeedbackBatchItem,
    SnowballSimulateRequest,
    DividendItem,
)

router = APIRouter(tags=["analytics"])

_benchmarks_cache: Optional[Dict[str, Any]] = None
_benchmarks_cache_time: float = 0.0
_telegram_run_lock = False

def _is_tg_locked() -> bool:
    try:
        import barsi.api.http as http_mod
        return bool(getattr(http_mod, "_telegram_run_lock", _telegram_run_lock))
    except Exception:
        return _telegram_run_lock

def _set_tg_locked(val: bool) -> None:
    global _telegram_run_lock
    _telegram_run_lock = val
    try:
        import barsi.api.http as http_mod
        http_mod._telegram_run_lock = val
    except Exception:
        pass

# ----------------- HOME SUMMARY & RADAR -----------------

@router.get("/api/home/summary")
async def get_home_summary(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autorizado")

    positions = await get_canonical_positions()

    soma_patrimonio = 0.0
    soma_cotas = 0
    soma_aporte = 0.0
    soma_novas_cotas = 0
    abaixo_teto = 0
    proventos_ano = 0.0
    validados = 0
    concordos = 0

    top_insights = []
    for p in positions:
        atualizar_posicao_quant(p, p.preco_atual, p.div_proj)
        qtd_antes = p.qtd_antes or 0
        qtd_aporte = p.qtd_aporte or 0
        cotacao = p.preco_atual or 0.0

        soma_patrimonio += qtd_antes * cotacao
        soma_cotas += qtd_antes
        soma_aporte += qtd_aporte * cotacao
        soma_novas_cotas += qtd_aporte
        proventos_ano += (qtd_antes + qtd_aporte) * (p.div_proj or 0.0)

        if p.status_barsi.value == "ACUMULAR":
            abaixo_teto += 1

        if p.feedback_usuario:
            validados += 1
            if "CONCORDO" in p.feedback_usuario.upper():
                concordos += 1

        top_insights.append({
            "ticker": p.ticker,
            "setor": p.setor,
            "empresa": p.empresa,
            "preco": round(cotacao, 2),
            "teto": round(p.preco_teto, 2),
            "margem": round(p.margem_seguranca, 1),
            "status": p.status_barsi.value,
            "insight": p.insight_atual or f"DY Projetado de {p.dy:.1f}%. Margem {p.margem_seguranca:.1f}%.",
            "insight_resumo": p.insight_atual or f"DY Projetado de {p.dy:.1f}%. Margem {p.margem_seguranca:.1f}%.",
            "feedback": p.feedback_usuario
        })

    taxa = round((concordos / validados * 100), 1) if validados > 0 else 100.0
    top_insights.sort(key=lambda x: x["margem"], reverse=True)

    return {
        "patrimonio_total": round(soma_patrimonio, 2),
        "cotas_totais": soma_cotas,
        "total_cotas": soma_cotas,
        "aporte_programado": round(soma_aporte, 2),
        "aporte_sugerido": round(soma_aporte, 2),
        "novas_cotas": soma_novas_cotas,
        "ativos_abaixo_teto": abaixo_teto,
        "total_ativos": len(positions),
        "proventos_proj_ano": round(proventos_ano, 2),
        "proventos_estimados_ano": round(proventos_ano, 2),
        "proventos_estimados_mes": round(proventos_ano / 12, 2) if proventos_ano > 0 else 0.0,
        "taxa_concordancia": taxa,
        "top_oportunidades": top_insights[:5],
        "top_insights": top_insights[:5],
        "radar_oportunidades": top_insights[:5]
    }

# ----------------- SIMULADOR & INSIGHTS -----------------

@router.get("/api/simulador")
async def get_simulador(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autorizado")
    
    try:
        sheets = AdapterFactory.create_sheets_adapter(settings)
        if hasattr(sheets, "load_simulador_orders"):
            orders = await sheets.load_simulador_orders()
            return JSONResponse(content=[o.model_dump() for o in orders])
        return JSONResponse(content=[])
    except Exception as e:
        logger.warning("api_simulador_fallback", error=str(e))
        return JSONResponse(content=[])

@router.get("/api/insights")
async def get_insights_api(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autorizado")

    positions = await get_canonical_positions()

    db_path = settings.sqlite_db_path
    db_items = {}
    if os.path.exists(db_path):
        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute("""
                    SELECT h1.* FROM historico_analises h1
                    JOIN (
                        SELECT ticker, MAX(id) as max_id FROM historico_analises GROUP BY ticker
                    ) h2 ON h1.id = h2.max_id
                    ORDER BY h1.margem_seguranca DESC
                """).fetchall()
                for r in rows:
                    db_items[r["ticker"]] = dict(r)
            except sqlite3.OperationalError:
                pass

    items = []
    concordo_count = 0
    validados_count = 0
    acumular_count = 0
    aguardar_count = 0

    for p in positions:
        atualizar_posicao_quant(p, p.preco_atual, p.div_proj)
        db_hist = db_items.get(p.ticker, {})
        insight_text = p.insight_atual or db_hist.get("insight_ia") or f"Score Quant B.E.S.T.: Margem {p.margem_seguranca:.1f}%, DY Proj {p.dy:.1f}%. Diretriz RL: {p.status_barsi.value}."
        feedback_val = p.feedback_usuario or db_hist.get("feedback_usuario", "")

        is_acumular = (p.status_barsi.value == "ACUMULAR")
        if is_acumular:
            acumular_count += 1
        else:
            aguardar_count += 1

        if feedback_val:
            validados_count += 1
            if "CONCORDO" in feedback_val.upper():
                concordo_count += 1

        items.append({
            "ticker": p.ticker,
            "empresa": p.empresa,
            "setor": p.setor,
            "preco": round(p.preco_atual, 2),
            "teto": round(p.preco_teto, 2),
            "margem": round(p.margem_seguranca, 1),
            "acao_sugerida": p.status_barsi.value,
            "insight_ia": insight_text,
            "feedback_usuario": feedback_val
        })

    taxa = round((concordo_count / validados_count * 100), 1) if validados_count > 0 else 100.0

    return {
        "taxa_concordancia": taxa,
        "total_count": len(items),
        "validados_count": validados_count,
        "concordo_count": concordo_count,
        "acumular_count": acumular_count,
        "aguardar_count": aguardar_count,
        "items": items
    }

@router.post("/api/insights/feedback/batch")
async def save_insight_feedback_batch(req: FeedbackBatchRequest, request: Request):
    require_role_action(request, action_type="edit")
    if not req.items:
        return {"ok": True, "count": 0}

    budget_repo = BudgetRepository(settings.sqlite_db_path)
    for it in req.items:
        budget_repo.update_insight_feedback(it.ticker, it.feedback)

    return {"ok": True, "count": len(req.items)}

@router.post("/api/insights/feedback")
async def save_insight_feedback(req: FeedbackRequest, request: Request):
    batch_req = FeedbackBatchRequest(items=[FeedbackBatchItem(ticker=req.ticker, feedback=req.feedback)])
    res = await save_insight_feedback_batch(batch_req, request)
    return {
        "ok": res.get("ok", True),
        "ticker": req.ticker,
        "feedback": req.feedback
    }

# ----------------- SNAPSHOTS & BENCHMARKS -----------------

def _record_snapshot_logic(positions: List[Position], repo: BudgetRepository) -> Dict[str, Any]:
    soma_patrimonio = sum((p.qtd_antes or 0) * (p.preco_atual or 0.0) for p in positions)
    soma_cotas = sum(p.qtd_antes or 0 for p in positions)
    if soma_patrimonio <= 0:
        logger.warning("snapshot_skipped_empty_portfolio", cotas=soma_cotas)
        return {"status": "skipped", "reason": "empty_portfolio"}

    soma_aporte = sum((p.qtd_aporte or 0) * (p.preco_atual or 0.0) for p in positions)
    proventos_ano = sum(((p.qtd_antes or 0) + (p.qtd_aporte or 0)) * (p.div_proj or 0.0) for p in positions)
    setores = {}
    for p in positions:
        sec = p.setor or "B.E.S.T."
        val = (p.qtd_antes or 0) * (p.preco_atual or 0.0)
        setores[sec] = round(setores.get(sec, 0.0) + val, 2)

    today_str = datetime.now().strftime("%Y-%m-%d")
    repo.save_portfolio_snapshot(
        today_str,
        round(soma_patrimonio, 2),
        soma_cotas,
        dividendos_mes=round(proventos_ano / 12, 2),
        aporte_planejado=round(soma_aporte, 2),
        setores_json=json.dumps(setores, ensure_ascii=False)
    )
    return {"status": "ok", "snapshot_date": today_str, "patrimonio": round(soma_patrimonio, 2)}

@router.post("/api/snapshot/today")
async def record_today_snapshot(request: Request):
    require_role_action(request, action_type="edit")
    repo = BudgetRepository(settings.sqlite_db_path)
    positions = await get_canonical_positions()
    return _record_snapshot_logic(positions, repo)

def get_real_benchmarks(snapshots: List[Dict[str, Any]]) -> Dict[str, Any]:
    import barsi.api.common as common
    try:
        import barsi.api.http as http_mod
        if getattr(http_mod, "_benchmarks_cache", None) is None:
            common._benchmarks_cache = None
            common._benchmarks_cache_time = 0.0
    except Exception:
        pass

    now = time.time()
    if common._benchmarks_cache and (now - common._benchmarks_cache_time) < 3600.0:
        return common._benchmarks_cache

    # 1. Retorno Carteira Real a partir dos snapshots do período
    carteira_ret = None
    if snapshots and len(snapshots) >= 2:
        val_inicial = float(snapshots[0].get("patrimonio_total") or 0.0)
        val_final = float(snapshots[-1].get("patrimonio_total") or 0.0)
        if val_inicial > 0:
            carteira_ret = round(((val_final / val_inicial) - 1.0) * 100.0, 2)

    # 2. IBOVESPA 12M (^BVSP) real via Yahoo Finance
    ibov_ret = None
    try:
        import yfinance as yf
        hist = yf.Ticker("^BVSP").history(period="1y", timeout=8)
        if hist is not None and not hist.empty and len(hist) > 10:
            c0 = float(hist["Close"].iloc[0])
            c1 = float(hist["Close"].iloc[-1])
            if c0 > 0:
                ibov_ret = round(((c1 / c0) - 1.0) * 100.0, 2)
    except Exception as e:
        logger.warn("ibov_benchmark_fetch_failed", error=str(e))

    # 3. IFIX 12M (IFIX.SA) real via Yahoo Finance
    ifix_ret = None
    try:
        import yfinance as yf
        hist_ifix = yf.Ticker("IFIX.SA").history(period="1y", timeout=8)
        if hist_ifix is not None and not hist_ifix.empty and len(hist_ifix) > 10:
            c0 = float(hist_ifix["Close"].iloc[0])
            c1 = float(hist_ifix["Close"].iloc[-1])
            if c0 > 0:
                ifix_ret = round(((c1 / c0) - 1.0) * 100.0, 2)
    except Exception as e:
        logger.warn("ifix_benchmark_fetch_failed", error=str(e))

    # 4. CDI 12M Real acumulado via Banco Central do Brasil (SGS Série 4390)
    cdi_ret = None
    try:
        bcb_url = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.4390/dados/ultimos/12?formato=json"
        req = urllib.request.Request(bcb_url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            bcb_data = json.loads(resp.read().decode("utf-8"))
            if bcb_data and len(bcb_data) >= 11:
                acum = 1.0
                for m in bcb_data[-12:]:
                    acum *= (1.0 + float(m["valor"]) / 100.0)
                cdi_ret = round((acum - 1.0) * 100.0, 2)
            else:
                logger.warn("cdi_bcb_insufficient_data", count=len(bcb_data) if bcb_data else 0)
    except Exception as e:
        logger.warn("cdi_bcb_fetch_failed", error=str(e))
        cdi_ret = None

    spread_cdi = round(carteira_ret - cdi_ret, 2) if (carteira_ret is not None and cdi_ret is not None) else None
    spread_ibov = round(carteira_ret - ibov_ret, 2) if (carteira_ret is not None and ibov_ret is not None) else None

    result = {
        "carteira_12m": carteira_ret,
        "cdi_12m": cdi_ret,
        "ibov_12m": ibov_ret,
        "ifix_12m": ifix_ret,
        "spread_cdi": spread_cdi,
        "spread_ibov": spread_ibov,
        "fonte": "Yahoo Finance (^BVSP, IFIX.SA) & Banco Central do Brasil (SGS 4390)"
    }
    common._benchmarks_cache = result
    common._benchmarks_cache_time = now
    try:
        import barsi.api.http as http_mod
        http_mod._benchmarks_cache = result
        http_mod._benchmarks_cache_time = now
    except Exception:
        pass
    return result

@router.get("/api/analytics/benchmarks")
async def get_benchmarks(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autorizado")
    budget_repo = BudgetRepository(settings.sqlite_db_path)
    snapshots = budget_repo.get_portfolio_snapshots(12)
    return await asyncio.to_thread(get_real_benchmarks, snapshots)

@router.get("/api/analytics/data")
async def get_analytics_data(request: Request, months: int = 12):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autorizado")

    positions = await get_canonical_positions()

    soma_patrimonio = 0.0
    soma_cotas = 0
    proventos_ano = 0.0
    setores = {}

    for p in positions:
        atualizar_posicao_quant(p, p.preco_atual, p.div_proj)
        val = (p.qtd_antes or 0) * (p.preco_atual or 0.0)
        soma_patrimonio += val
        soma_cotas += (p.qtd_antes or 0)
        proventos_ano += ((p.qtd_antes or 0) + (p.qtd_aporte or 0)) * (p.div_proj or 0.0)

        setor = p.setor or "B.E.S.T."
        setores[setor] = round(setores.get(setor, 0.0) + val, 2)

    budget_repo = BudgetRepository(settings.sqlite_db_path)
    if not positions or soma_patrimonio <= 0:
        snapshots = []
    else:
        snapshots = budget_repo.build_real_portfolio_snapshots(limit_months=months)
        if not snapshots or len(snapshots) < 3:
            budget_repo.seed_initial_snapshots_if_empty(round(soma_patrimonio, 2), soma_cotas, setores)
            snapshots = budget_repo.get_portfolio_snapshots(months)

    dividendos = []
    for s in (snapshots[-6:] if len(snapshots) >= 6 else snapshots):
        ym_parts = s["data_snapshot"].split("-")
        mes_str = f"{ym_parts[1]}/{ym_parts[0][2:]}" if len(ym_parts) >= 2 else s["data_snapshot"]
        div_real = s.get("dividendos_mes", 0.0)
        growth_multiplier = 1.0 + settings.div_growth_projection_rate
        dividendos.append({
            "mes": mes_str,
            "realizado": round(div_real, 2),
            "projetado": round(div_real * growth_multiplier, 2)
        })

    return {
        "patrimonio_atual": round(soma_patrimonio, 2),
        "proventos_proj_ano": round(proventos_ano, 2),
        "snapshots": snapshots,
        "setores": setores,
        "dividendos": dividendos
    }

# ----------------- ARTEFATOS PERSONALIZADOS -----------------

@router.get("/api/artifacts/sources")
async def get_artifact_sources(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autorizado")
    repo = BudgetRepository(settings.sqlite_db_path)
    return repo.get_available_data_sources()

@router.get("/api/artifacts")
async def list_artifacts(request: Request, visible_in: Optional[str] = None):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autorizado")
    repo = BudgetRepository(settings.sqlite_db_path)
    items = repo.list_custom_artifacts(visible_in=visible_in)
    return {"items": items}

@router.post("/api/artifacts")
async def create_artifact(request: Request, payload: ArtifactCreateRequest):
    user = require_role_action(request, action_type="edit")
    repo = BudgetRepository(settings.sqlite_db_path)
    try:
        artifact_id = repo.create_custom_artifact(
            name=payload.name,
            chart_type=payload.chart_type,
            data_source=payload.data_source,
            fields=payload.fields,
            description=payload.description,
            filters=payload.filters,
            visible_in=payload.visible_in
        )
        return {"ok": True, "id": artifact_id, "message": "Artefato criado com sucesso"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=400, detail=f"Já existe um artefato com o nome '{payload.name}'")

@router.delete("/api/artifacts/{artifact_id}")
async def delete_artifact(request: Request, artifact_id: int):
    user = require_role_action(request, action_type="edit")
    repo = BudgetRepository(settings.sqlite_db_path)
    deleted = repo.delete_custom_artifact(artifact_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Artefato não encontrado")
    return {"ok": True, "message": "Artefato excluído com sucesso"}

@router.get("/api/artifacts/{artifact_id}/data")
async def get_artifact_data(request: Request, artifact_id: int):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autorizado")
    repo = BudgetRepository(settings.sqlite_db_path)
    try:
        return repo.get_custom_artifact_data(artifact_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@router.get("/api/artifacts/{artifact_id}/export")
async def export_artifact_excel(request: Request, artifact_id: int):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autorizado")
    repo = BudgetRepository(settings.sqlite_db_path)
    try:
        data = repo.get_custom_artifact_data(artifact_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

    rows = data.get("rows") or []
    if rows:
        df = pd.DataFrame(rows)
    else:
        df = pd.DataFrame(columns=data.get("fields") or ["dados"])

    output = io.BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Dados", index=False)
    output.seek(0)

    safe_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in data.get("name", f"artefato_{artifact_id}"))
    filename = f"{safe_name}_{artifact_id}.xlsx"

    return StreamingResponse(
        output,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )

# ----------------- EARLY WARNING & SNOWBALL -----------------

@router.get("/api/early-warning/alerts")
async def list_early_warning_alerts(request: Request, active_only: bool = True):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autorizado")
    repo = BudgetRepository(settings.sqlite_db_path)
    news = AdapterFactory.create_news_adapter(settings)
    service = EarlyWarningService(repo, news)
    return JSONResponse(content=service.list_alerts(active_only=active_only))

@router.post("/api/early-warning/scan")
async def scan_early_warning_portfolio(request: Request):
    require_role_action(request, action_type="edit")
    repo = BudgetRepository(settings.sqlite_db_path)
    news = AdapterFactory.create_news_adapter(settings)
    notifier = AdapterFactory.create_notification_adapter(settings)
    service = EarlyWarningService(repo, news, notifier)
    alerts = await service.scan_portfolio()
    return JSONResponse(content={"status": "ok", "alerts_count": len(alerts), "alerts": alerts})

@router.post("/api/early-warning/{alert_id}/dismiss")
async def dismiss_early_warning_alert(alert_id: int, request: Request):
    require_role_action(request, action_type="edit")
    repo = BudgetRepository(settings.sqlite_db_path)
    news = AdapterFactory.create_news_adapter(settings)
    service = EarlyWarningService(repo, news)
    ok = service.dismiss_alert(alert_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Alerta não encontrado")
    return JSONResponse(content={"status": "ok"})

@router.post("/api/snowball/simulate")
async def simulate_snowball_api(req: SnowballSimulateRequest, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autorizado")
    repo = BudgetRepository(settings.sqlite_db_path)
    service = SnowballBacktestService(repo)
    try:
        res = service.simulate_snowball(
            aporte_mensal=req.aporte_mensal,
            taxa_reinvestimento=req.taxa_reinvestimento,
            anos=req.anos,
            dy_base=req.dy_base,
            crescimento_anual_proventos=req.crescimento_anual_proventos,
            valor_inicial=req.valor_inicial
        )
        return JSONResponse(content=res)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

# ----------------- PROVENTOS REAIS (DIVIDENDOS) -----------------

@router.get("/api/dividends")
async def get_dividends_endpoint(
    request: Request,
    ticker: Optional[str] = None,
    mes_ano: Optional[str] = None,
    year: Optional[int] = None
):
    """Retorna o histórico de proventos reais recebidos, agrupamentos mensais e total geral."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autorizado")
    repo = BudgetRepository(settings.sqlite_db_path)
    records = repo.list_dividends_received(ticker=ticker, mes_ano=mes_ano, year=year)
    monthly_summary = repo.get_dividends_summary_by_month(months=12)
    total = repo.get_total_dividends_received(year=year)
    return {
        "total": total,
        "monthly_summary": monthly_summary,
        "records": records
    }

@router.post("/api/dividends")
async def record_dividend_endpoint(req: DividendItem, request: Request):
    """Registra manualmente ou importa um dividendo real recebido no SQLite."""
    require_role_action(request, action_type="edit")
    repo = BudgetRepository(settings.sqlite_db_path)
    new_id = repo.record_dividend_received(
        ticker=req.ticker,
        data_pagamento=req.data_pagamento,
        valor_total=req.valor_total,
        valor_por_cota=req.valor_por_cota or 0.0,
        quantidade=req.quantidade or 0,
        tipo=req.tipo or "DIVIDENDO",
        fonte=req.fonte or "manual"
    )
    return {"ok": True, "id": new_id, "ticker": req.ticker, "valor_total": req.valor_total}

# ----------------- RECARGA DE MODELOS & PIPELINE DE ANÁLISE -----------------

@router.post("/api/dss/reload")
async def reload_dss(request: Request):
    """Recarrega os pesos do modelo DividendSustainabilityScorer em tempo de execução."""
    require_role_action(request, action_type="admin")
    from barsi.domain.quant_ai import DividendSustainabilityScorer
    DividendSustainabilityScorer.reload()
    return {"ok": True, "message": "Pesos DSS recarregados com sucesso"}

@router.post("/api/run")
async def trigger_run_web(request: Request):
    require_role_action(request, action_type="run")
    import barsi.api.common as common_mod
    service = get_sync_service()
    summary = await service.execute_run()
    common_mod.LAST_RUN_SUMMARY = summary
    try:
        import barsi.api.http as http_mod
        http_mod.LAST_RUN_SUMMARY = summary
    except Exception:
        pass
    invalidate_canonical_positions_cache()

    try:
        repo = BudgetRepository(settings.sqlite_db_path)
        positions = await get_canonical_positions()
        _record_snapshot_logic(positions, repo)
    except Exception as exc:
        logger.warning("run_snapshot_save_failed", error=str(exc))

    return summary.model_dump()

# ----------------- WEBHOOK INTERATIVO TELEGRAM BOT -----------------

async def send_telegram_message(bot_token: str, chat_id: str, reply_text: str):
    """Envia mensagem assíncrona de resposta para o Telegram Bot."""
    if not bot_token:
        return
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        import httpx
        async with httpx.AsyncClient(timeout=8.0) as client:
            await client.post(url, json={
                "chat_id": chat_id,
                "text": reply_text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True
            })
    except Exception as err:
        logger.error("telegram_reply_failed", error=str(err))

@router.post("/api/telegram/webhook")
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks):
    """Webhook do Telegram Bot para comandos interativos (/status, /radar, /proventos, /ticker, /run)."""
    expected_secret = (settings.telegram_webhook_secret or "").strip()
    if expected_secret:
        received_secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "").strip()
        if received_secret != expected_secret:
            logger.warning("telegram_invalid_webhook_secret", ip=request.client.host if request.client else "")
            raise HTTPException(status_code=403, detail="invalid_webhook_secret")

    try:
        data = await request.json()
    except Exception:
        return {"ok": False, "error": "invalid_json"}

    msg = data.get("message") or data.get("edited_message") or {}
    chat_id = str(msg.get("chat", {}).get("id") or "")
    text = (msg.get("text") or "").strip()

    if not chat_id or not text:
        return {"ok": True}

    configured_chat = str(settings.telegram_chat_id or "").strip()
    if not configured_chat or chat_id != configured_chat:
        logger.warning("telegram_unauthorized_chat", chat_id=chat_id)
        return {"ok": True}

    cmd = text.split()[0].lower()
    args = text.split()[1:]
    repo = BudgetRepository(settings.sqlite_db_path)
    bot_token = settings.telegram_bot_token

    async def _send_reply(reply_text: str):
        try:
            import barsi.api.http as http_mod
            sender = getattr(http_mod, "send_telegram_message", send_telegram_message)
        except Exception:
            sender = send_telegram_message
        await sender(bot_token, chat_id, reply_text)

    if cmd in ("/status", "/carteira"):
        positions = await get_canonical_positions()
        patrimonio = sum((p.qtd_antes or 0) * (p.preco_atual or 0.0) for p in positions)
        proventos = sum(((p.qtd_antes or 0) + (p.qtd_aporte or 0)) * (p.div_proj or 0.0) for p in positions)
        acumular = sum(1 for p in positions if p.status_barsi.value == "ACUMULAR")
        resposta = (
            f"💼 *Cockpit Barsi Quant IA*\n\n"
            f"• *Patrimônio:* R$ {patrimonio:,.2f}\n"
            f"• *Ativos Monitorados:* {len(positions)}\n"
            f"• *Em Zona de Aporte:* {acumular}\n"
            f"• *Proventos Proj. Ano:* R$ {proventos:,.2f}"
        )
        await _send_reply(resposta)

    elif cmd == "/radar":
        positions = await get_canonical_positions()
        for p in positions:
            atualizar_posicao_quant(p, p.preco_atual, p.div_proj)
        acumular = [p for p in positions if p.status_barsi.value == "ACUMULAR"]
        acumular.sort(key=lambda x: x.margem_seguranca, reverse=True)
        if not acumular:
            resposta = "🎯 *Radar B.E.S.T.*: Nenhum ativo abaixo do Preço Teto no momento."
        else:
            linhas = [f"• *{p.ticker}*: R$ {p.preco_atual:.2f} (Teto R$ {p.preco_teto:.2f} · Margem: +{p.margem_seguranca:.1f}%)" for p in acumular[:6]]
            resposta = "🎯 *Radar B.E.S.T. (Top Oportunidades)*\n\n" + "\n".join(linhas)
        await _send_reply(resposta)

    elif cmd == "/proventos":
        total_hist = repo.get_total_dividends_received()
        ano_atual = datetime.now().year
        total_ano = repo.get_total_dividends_received(year=ano_atual)
        resposta = (
            f"💵 *Histórico de Proventos Reais Recebidos*\n\n"
            f"• *Total em {ano_atual}:* R$ {total_ano:,.2f}\n"
            f"• *Acumulado Histórico:* R$ {total_hist:,.2f}"
        )
        await _send_reply(resposta)

    elif cmd == "/ticker":
        if not args:
            await _send_reply("⚠️ Uso correto: `/ticker BBAS3`")
        else:
            t_alvo = args[0].strip().upper()
            positions = await get_canonical_positions()
            p = next((x for x in positions if x.ticker == t_alvo), None)
            if not p:
                await _send_reply(f"❌ Ativo *{t_alvo}* não encontrado na carteira.")
            else:
                atualizar_posicao_quant(p, p.preco_atual, p.div_proj)
                resposta = (
                    f"📊 *Análise Barsi: {p.ticker}*\n\n"
                    f"• *Empresa:* {p.empresa or p.setor}\n"
                    f"• *Cotação Atual:* R$ {p.preco_atual:.2f}\n"
                    f"• *Preço Teto:* R$ {p.preco_teto:.2f}\n"
                    f"• *Margem de Segurança:* {p.margem_seguranca:.1f}%\n"
                    f"• *DPA Projetado:* R$ {p.div_proj:.2f}\n"
                    f"• *Decisão Barsi:* `{p.status_barsi.value}`"
                )
                await _send_reply(resposta)

    elif cmd == "/run":
        if _is_tg_locked():
            await _send_reply("⏳ Já existe uma análise quantitativa em andamento na TV Box. Por favor, aguarde.")
            return {"ok": True}

        _set_tg_locked(True)
        await _send_reply("🚀 Ciclo de análise quantitativa disparado em segundo plano...")
        async def _run_task():
            try:
                summary = await get_sync_service().execute_run()
                acumular_cnt = summary.actions_recommended.get("ACUMULAR", 0) if getattr(summary, "actions_recommended", None) else 0
                await _send_reply(
                    f"✅ *Ciclo Concluído com Sucesso!*\n\n"
                    f"• Ativos analisados: {summary.tickers_analyzed}\n"
                    f"• Em zona de aporte: {acumular_cnt}\n"
                    f"• Duração: {summary.duration_ms}ms"
                )
            except Exception as e:
                logger.error("telegram_run_task_failed", error=str(e))
                await _send_reply(f"❌ Falha durante execução do ciclo: {str(e)}")
            finally:
                _set_tg_locked(False)

        background_tasks.add_task(_run_task)

    elif cmd == "/schedule":
        cfg = repo.get_scheduler_config()
        if not args:
            next_run = repo.next_scheduled_run() or "—"
            await _send_reply(
                f"⏰ *Scheduler Barsi*\n\n"
                f"• Status: {'🟢 ON' if cfg['enabled'] else '🔴 OFF'}\n"
                f"• Dias (ISO): `{cfg['days_of_week']}`\n"
                f"• Horas: `{cfg['hours']}`\n"
                f"• Minuto: `{cfg['minute']}`\n"
                f"• Próximo: `{next_run}`\n\n"
                f"Uso:\n"
                f"`/schedule on|off`\n"
                f"`/schedule days 1,2,3,4,5`\n"
                f"`/schedule hours 9,10,11,12,13,14,15,16,17,18`\n"
                f"`/schedule minute 30`"
            )
        elif len(args) == 1 and args[0] in ("on", "off"):
            repo.update_scheduler_config(enabled=(args[0] == "on"), updated_by=f"telegram:{chat_id}")
            await _send_reply(f"✅ Scheduler {'ativado' if args[0] == 'on' else 'desativado'}.")
        elif len(args) >= 2:
            key, val = args[0], " ".join(args[1:])
            try:
                if key == "days":
                    repo.update_scheduler_config(days_of_week=val, updated_by=f"telegram:{chat_id}")
                elif key == "hours":
                    repo.update_scheduler_config(hours=val, updated_by=f"telegram:{chat_id}")
                elif key == "minute":
                    repo.update_scheduler_config(minute=int(val), updated_by=f"telegram:{chat_id}")
                else:
                    await _send_reply("⚠️ Chaves válidas: days | hours | minute")
                    return {"ok": True}
                await _send_reply(f"✅ `{key}` = `{val}` gravado.")
            except Exception as e:
                await _send_reply(f"⚠️ Erro: {e}")
        else:
            await _send_reply("⚠️ Uso: `/schedule`, `/schedule on|off`, `/schedule days|hours|minute <valor>`")

    elif cmd == "/notifications":
        if not args:
            prefs = repo.list_notification_preferences()
            linhas = [
                f"• `{p['alert_type']}`: {'🟢 ON' if p['enabled'] else '🔴 OFF'}"
                for p in prefs
            ] or ["_(nenhuma preferência configurada — todas ON por padrão)_"]
            await _send_reply(
                "🔔 *Preferências de Notificação*\n\n" + "\n".join(linhas) +
                "\n\nUso:\n"
                "`/notifications on` ou `/notifications off`\n"
                "`/notifications margem_20 off`"
            )
        elif len(args) == 1 and args[0] in ("on", "off"):
            enabled = args[0] == "on"
            count = repo.set_all_notifications_enabled(enabled, updated_by=f"telegram:{chat_id}")
            await _send_reply(f"✅ Todas as notificações {'ativadas' if enabled else 'desativadas'} ({count} tipos).")
        elif len(args) == 2 and args[1] in ("on", "off"):
            alert_type, state = args[0], args[1]
            enabled = state == "on"
            try:
                repo.set_notification_enabled(alert_type, enabled, updated_by=f"telegram:{chat_id}")
                await _send_reply(f"✅ `{alert_type}` agora está {'🟢 ON' if enabled else '🔴 OFF'}.")
            except ValueError as e:
                await _send_reply(f"⚠️ {e}")
        else:
            await _send_reply("⚠️ Uso: `/notifications`, `/notifications on|off`, `/notifications <tipo> on|off`")

    else:
        ajuda = (
            "🤖 *Comandos do Barsi Quant Bot:*\n\n"
            "• `/status` — Visão geral da carteira e patrimônio\n"
            "• `/radar` — Ativos com maior margem de segurança\n"
            "• `/proventos` — Total de dividendos recebidos\n"
            "• `/ticker BBAS3` — Detalhes completos de um ativo\n"
            "• `/schedule` — Configurar agendamento automático\n"
            "• `/notifications` — Gerenciar preferências de alerta\n"
            "• `/run` — Disparar ciclo quantitativo B3 agora\n"
            "• `/help` — Exibir este menu de ajuda"
        )
        await _send_reply(ajuda)

    return {"ok": True}

