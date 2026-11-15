"""Rotas de carteira, custódia B3, DRIP, exportações (Sheets/Excel) e sincronização histórica."""
import os
from fastapi import APIRouter, Request, HTTPException, UploadFile, File, BackgroundTasks
from fastapi.responses import JSONResponse, FileResponse

from barsi.config import settings
from barsi.infra.logging import logger
from barsi.adapters.factory import AdapterFactory
from barsi.adapters.history.budget_repository import BudgetRepository
from barsi.domain.models import Position
from barsi.domain.rules import atualizar_posicao_quant
from barsi.services.drip_service import DripService
from barsi.services.b3_custodia_service import B3CustodiaService
from barsi.api.common import (
    get_current_user,
    require_role_action,
    get_canonical_positions,
    invalidate_canonical_positions_cache,
    EditItem,
    SaveEditsRequest,
    DripSimulateRequest,
    DripApplyRequest,
)

router = APIRouter(tags=["carteira"])

# ----------------- ROTAS DE CUSTÓDIA & CARTEIRA -----------------

@router.get("/api/carteira")
async def get_carteira(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autorizado")
    
    positions = await get_canonical_positions()
    
    data = []
    is_leitor = (user.get("role") == "leitor")
    for p in positions:
        atualizar_posicao_quant(p, p.preco_atual, p.div_proj)
        data.append({
            "ticker": p.ticker,
            "row_index": p.row_index,
            "empresa": p.empresa,
            "setor": p.setor,
            "qtd_antes": p.qtd_antes,
            "qtd_aporte": p.qtd_aporte,
            "qtd_total": p.qtd_total,
            "preco": round(p.preco_atual, 2),
            "teto": round(p.preco_teto, 2),
            "margem": round(p.margem_seguranca, 1),
            "dy": round(p.dy, 2),
            "custo_medio": round(getattr(p, "custo_medio", 0.0) or 0.0, 2),
            "yield_on_cost": round(getattr(p, "yield_on_cost", 0.0) or 0.0, 2),
            "valor_atual": round((p.qtd_antes or 0) * p.preco_atual, 2),
            "valor_total": round(p.valor_total, 2),
            "total_aporte": round(p.total_aporte, 2),
            "status": p.status_barsi.value,
            "div_proj": round(p.div_proj, 2),
            "feedback": p.feedback_usuario,
            "insight_atual": p.insight_atual,
            "teto_manual": getattr(p, "teto_manual", False),
            "is_readonly": is_leitor
        })
    data.sort(key=lambda x: x["margem"], reverse=True)
    return JSONResponse(content=data)

@router.post("/api/carteira")
async def save_carteira_edits(req: SaveEditsRequest, request: Request):
    require_role_action(request, action_type="edit")
    
    budget_repo = BudgetRepository(settings.sqlite_db_path)
    positions = await get_canonical_positions()
    pos_map = {p.ticker: p for p in positions}

    for item in req.items:
        preco_ref = item.preco_atual or (pos_map[item.ticker].preco_atual if item.ticker in pos_map else 0.0)
        if item.div_proj is not None and item.div_proj > 0:
            if preco_ref <= 0:
                raise HTTPException(
                    status_code=400,
                    detail=f"Para {item.ticker}, informe preco_atual maior que zero antes de definir div_proj (necessário para validação de sanidade do DY)."
                )
            dy_calc = item.div_proj / preco_ref
            if dy_calc > settings.max_plausible_dy:
                logger.warning("dy_implausible_rejected", ticker=item.ticker, div_proj=item.div_proj, preco=preco_ref, dy=dy_calc)
                raise HTTPException(
                    status_code=400,
                    detail=f"DPA Projetado R$ {item.div_proj:.2f} resulta em DY de {dy_calc:.1%} para {item.ticker} (máx {settings.max_plausible_dy:.0%}). Verifique se não digitou valor acumulado ou mensal incorreto."
                )

        if item.ticker in pos_map:
            p = pos_map[item.ticker]
            if item.div_proj is not None:
                p.div_proj = item.div_proj
            if item.preco_atual is not None and item.preco_atual > 0:
                p.preco_atual = item.preco_atual
            if item.teto_manual is not None:
                p.teto_manual = item.teto_manual
            if item.custo_medio is not None and item.custo_medio >= 0:
                p.custo_medio = item.custo_medio
            if item.preco_teto is not None and item.preco_teto > 0:
                p.preco_teto = item.preco_teto
                p.teto_manual = True
            if item.margem is not None:
                p.margem_seguranca = item.margem
                if p.preco_atual > 0:
                    p.preco_teto = round(p.preco_atual * (1.0 + (item.margem / 100.0)), 2)
                    p.teto_manual = True
            if item.patrimonio is not None and item.patrimonio >= 0:
                if p.preco_atual > 0:
                    p.qtd_antes = max(0, int(round(item.patrimonio / p.preco_atual)))
                p.valor_total = round(item.patrimonio, 2)
            if item.qtd_antes is not None:
                p.qtd_antes = max(0, item.qtd_antes)
            if item.qtd_aporte is not None:
                p.qtd_aporte = max(0, item.qtd_aporte)
            p.qtd_total = p.qtd_antes + p.qtd_aporte
            p.valor_total = round(p.qtd_total * p.preco_atual, 2)
            p.total_aporte = round(p.qtd_aporte * p.preco_atual, 2)
            if item.feedback:
                p.feedback_usuario = item.feedback
            atualizar_posicao_quant(p, p.preco_atual, p.div_proj)
        else:
            preco = item.preco_atual if (item.preco_atual is not None and item.preco_atual > 0) else 0.0
            div_p = item.div_proj or 0.0
            teto = item.preco_teto or 0.0
            teto_man = bool(item.teto_manual) or (item.preco_teto is not None and item.preco_teto > 0)
            if item.margem is not None:
                if preco > 0:
                    teto = round(preco * (1.0 + (item.margem / 100.0)), 2)
                    teto_man = True

            q_antes = max(0, item.qtd_antes or 0)
            if item.patrimonio is not None and item.patrimonio >= 0 and preco > 0:
                q_antes = max(0, int(round(item.patrimonio / preco)))

            p = Position(
                ticker=item.ticker.strip().upper(),
                row_index=item.row_index if item.row_index and item.row_index >= 10 else 10 + len(positions),
                preco_atual=preco,
                div_proj=div_p,
                qtd_antes=q_antes,
                qtd_aporte=max(0, item.qtd_aporte or 0),
                preco_teto=teto,
                teto_manual=teto_man,
                custo_medio=item.custo_medio or 0.0,
                feedback_usuario=item.feedback
            )
            p.qtd_total = p.qtd_antes + p.qtd_aporte
            p.valor_total = round(p.qtd_total * p.preco_atual, 2)
            p.total_aporte = round(p.qtd_aporte * p.preco_atual, 2)
            atualizar_posicao_quant(p, p.preco_atual, p.div_proj)
            positions.append(p)
            pos_map[p.ticker] = p

    # SQLite como FONTE DA VERDADE ÚNICA (Persistência atômica local em <5ms)
    budget_repo.save_positions(positions)
    invalidate_canonical_positions_cache(user_id=budget_repo.user_id)

    return {
        "status": "ok",
        "updated_count": len(req.items)
    }

@router.put("/api/carteira/{ticker}")
async def update_single_carteira_position(ticker: str, item: EditItem, request: Request):
    """Atualiza ou cria uma única posição da carteira."""
    require_role_action(request, action_type="edit")
    item.ticker = ticker.strip().upper()
    return await save_carteira_edits(SaveEditsRequest(items=[item]), request)

@router.delete("/api/carteira/{ticker}")
async def delete_carteira_position(ticker: str, request: Request):
    """Exclui uma posição da carteira pelo ticker e invalida o cache de posições."""
    require_role_action(request, action_type="edit")
    budget_repo = BudgetRepository(settings.sqlite_db_path)
    success = budget_repo.delete_position(ticker)
    if not success:
        raise HTTPException(status_code=404, detail=f"Ativo {ticker} não encontrado na carteira.")
    invalidate_canonical_positions_cache(user_id=budget_repo.user_id)
    return {"status": "ok", "message": f"Ativo {ticker} excluído com sucesso."}

@router.post("/api/carteira/import-b3")
async def import_b3_custodia_endpoint(
    request: Request,
    file: UploadFile = File(...)
):
    """Importa e concilia a planilha oficial de custódia da Área do Investidor da B3 (.xlsx)."""
    require_role_action(request, action_type="edit")
    if not file.filename or not file.filename.lower().endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="Formato inválido. Envie o arquivo Excel (.xlsx) exportado da B3.")

    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Arquivo vazio.")

    budget_repo = BudgetRepository(settings.sqlite_db_path)
    service = B3CustodiaService(budget_repo)
    result = service.sync_custodia_to_sqlite(content)
    invalidate_canonical_positions_cache(user_id=budget_repo.user_id)
    return JSONResponse(content=result)

# ----------------- ROTAS DRIP & REINVESTIMENTO AUTOMÁTICO -----------------

@router.post("/api/drip/simulate")
async def simulate_drip_api(req: DripSimulateRequest, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autorizado")
    repo = BudgetRepository(settings.sqlite_db_path)
    service = DripService(repo)
    result = service.simulate_drip(
        valor_disponivel=req.valor_disponivel,
        mes_ano=req.mes_ano,
        balance_sectors=req.balance_sectors,
        max_ativos=req.max_ativos
    )
    return JSONResponse(content=result)

@router.post("/api/drip/apply")
async def apply_drip_api(req: DripApplyRequest, request: Request):
    require_role_action(request, action_type="edit")
    repo = BudgetRepository(settings.sqlite_db_path)
    service = DripService(repo)
    orders_dicts = [o.model_dump() for o in req.ordens]
    res = service.apply_drip_orders(orders_dicts)
    invalidate_canonical_positions_cache(user_id=repo.user_id)
    return JSONResponse(content=res)

# ----------------- EXPORTAÇÕES E SINCRONIZAÇÃO HISTÓRICA -----------------

@router.post("/api/export/sheets")
async def export_to_sheets(request: Request):
    """Exporta unidirecionalmente as posições e métricas do SQLite para o Google Sheets."""
    require_role_action(request, action_type="edit")
    repo = BudgetRepository(settings.sqlite_db_path)
    positions = repo.load_positions()
    if not positions:
        raise HTTPException(status_code=400, detail="Nenhuma posição no SQLite para exportar.")
    try:
        sheets = AdapterFactory.create_sheets_adapter(settings)
        await sheets.update_positions(positions, invalidate=True)
        return {"ok": True, "message": f"{len(positions)} ativos exportados com sucesso para o Google Sheets!", "count": len(positions)}
    except Exception as e:
        logger.error("export_sheets_failed", error=str(e))
        raise HTTPException(status_code=500, detail=f"Erro ao exportar para o Google Sheets: {str(e)}")

@router.get("/api/export/xlsx")
async def export_xlsx(request: Request):
    """Exporta as posições do SQLite para planilha Excel local (.xlsx) para download."""
    require_role_action(request, action_type="read")
    repo = BudgetRepository(settings.sqlite_db_path)
    positions = repo.load_positions()
    if not positions:
        raise HTTPException(status_code=400, detail="Nenhuma posição no SQLite para exportar.")
    
    from barsi.adapters.sheets.xlsx_adapter import XLSXAdapter, XLSX_HEADERS
    import openpyxl

    xlsx_path = getattr(settings, "local_xlsx_path", None) or "carteira_barsi_quant_ia.xlsx"
    if not os.path.isabs(xlsx_path):
        for candidate in [
            xlsx_path,
            os.path.join("barsi_quant_ia", xlsx_path),
            os.path.join(os.path.dirname(__file__), "../../../", xlsx_path),
            os.path.join(os.path.dirname(__file__), "../../../barsi_quant_ia", xlsx_path),
        ]:
            if os.path.exists(candidate):
                xlsx_path = os.path.abspath(candidate)
                break
        else:
            xlsx_path = os.path.abspath(xlsx_path)

    if not os.path.exists(xlsx_path):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Screener_BEST_Custodia"
        for col_idx, h in enumerate(XLSX_HEADERS, start=1):
            ws.cell(row=9, column=col_idx, value=h)
        for idx, p in enumerate(positions):
            r = p.row_index if (p.row_index and p.row_index >= 10) else (10 + idx)
            ws[f"A{r}"] = p.ticker
            ws[f"B{r}"] = p.setor
            ws[f"C{r}"] = p.empresa
        wb.save(xlsx_path)
        wb.close()

    adapter = XLSXAdapter(xlsx_path)
    await adapter.update_positions(positions)
    return FileResponse(xlsx_path, filename="carteira_barsi_quant_ia.xlsx", media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

@router.post("/api/history/sync")
async def sync_market_history_endpoint(request: Request, background_tasks: BackgroundTasks):
    require_role_action(request, action_type="run")
    repo = BudgetRepository(settings.sqlite_db_path)
    def _do_sync():
        try:
            count = repo.sync_yfinance_history()
            repo.build_real_portfolio_snapshots()
            logger.info("market_history_synced_background", records=count)
        except Exception as e:
            logger.error("market_history_sync_failed", error=str(e))
    background_tasks.add_task(_do_sync)
    return {"status": "queued", "message": "Sincronização de histórico de mercado agendada em segundo plano"}


@router.get("/api/payout/score/{ticker}")
async def get_payout_score(ticker: str, request: Request):
    """Retorna o score de sustentabilidade de dividendos v2 (Regressão Logística) com explicabilidade completa."""
    require_role_action(request, action_type="read")

    positions = await get_canonical_positions()
    pos = next((p for p in positions if p.ticker.upper() == ticker.upper()), None)
    if not pos:
        raise HTTPException(status_code=404, detail=f"Ativo {ticker.upper()} não encontrado na carteira")

    from barsi.domain.payout_scorer import PayoutScorer
    from barsi.domain.quant_ai import StatisticalBarsiEngine

    atualizar_posicao_quant(pos, pos.preco_atual, pos.div_proj)
    setor_classificado = StatisticalBarsiEngine.identificar_setor(pos.ticker)
    perenidade = StatisticalBarsiEngine.SETOR_STATS.get(
        setor_classificado, StatisticalBarsiEngine.SETOR_STATS.get("OUTROS", {})
    ).get("perenidade", 0.85)

    scorer = PayoutScorer.get_instance()
    result = scorer.score(pos, extras={"perenidade": perenidade})

    return {
        "ticker": pos.ticker,
        "empresa": pos.empresa,
        "setor": pos.setor,
        "setor_classificado": setor_classificado,
        "score": result["score"],
        "label": result["label"],
        "badge": result["badge"],
        "logit": result["logit"],
        "intercept": result["intercept"],
        "contributions": result["contributions"],
        "feature_summary": scorer.feature_summary,
        "is_loaded": result["is_loaded"],
        "weights_path": os.path.basename(scorer.weights_path) if scorer.weights_path else None,
    }


