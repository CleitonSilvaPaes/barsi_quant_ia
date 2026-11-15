"""Rotas de análise patrimonial, quant IA, benchmarks, snowball, dividendos e execução de pipeline."""
import time
import json
import asyncio
import urllib.request
from datetime import datetime
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse

from barsi.config import settings
from barsi.infra.logging import logger
from barsi.adapters.factory import AdapterFactory
from barsi.adapters.history.budget_repository import BudgetRepository
from barsi.domain.models import Position
from barsi.domain.rules import atualizar_posicao_quant
from barsi.services.snowball_backtest_service import SnowballBacktestService
from barsi.api.common import (
    get_current_user,
    require_role_action,
    get_canonical_positions,
    invalidate_canonical_positions_cache,
    get_sync_service,
    SnowballSimulateRequest,
    DividendItem,
)

router = APIRouter(tags=["analytics"])

_benchmarks_cache: Optional[Dict[str, Any]] = None
_benchmarks_cache_time: float = 0.0

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

# ----------------- SNOWBALL & DIVIDENDOS -----------------

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
