"""Rotas de orçamento mensal, extratos OFX, transações, categorias, fechamento e metas."""
import os
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Request, Response, HTTPException, UploadFile, File
from fastapi.responses import HTMLResponse

from barsi.config import settings
from barsi.infra.logging import logger
from barsi.adapters.factory import AdapterFactory
from barsi.adapters.history.budget_repository import BudgetRepository, validate_target_month
from barsi.services.ofx_service import OFXParserService
from barsi.services.monthly_closing_service import MonthlyClosingService
from barsi.api.common import (
    get_current_user,
    require_role_action,
    get_user_budget_repo,
    invalidate_canonical_positions_cache,
    GoalCreateRequest,
    GoalUpdateRequest,
    UpdateTransacaoRequest,
    CategoryRequest,
)

router = APIRouter(tags=["budget"])

# ----------------- ROTAS DE ORÇAMENTO E EXTRATOS -----------------

@router.get("/api/budget")
@router.get("/api/budget/{mes_ano}")
async def get_budget_endpoint(request: Request, mes_ano: Optional[str] = None):
    """Retorna comparativo Previsto vs Realizado (Orçamento Barsi) com base em despesas e transações."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autorizado")
    repo = BudgetRepository(settings.sqlite_db_path)
    return repo.get_budget_overview(mes_ano=mes_ano)

@router.get("/api/extratos/transacoes")
async def get_transacoes_endpoint(
    request: Request,
    mes_ano: Optional[str] = None,
    categoria: Optional[str] = None,
    search: Optional[str] = None,
    limit: int = 10,
    offset: int = 0
):
    """Lista as transações bancárias/cartão persistidas no SQLite com paginação e busca."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autorizado")
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    repo = BudgetRepository(settings.sqlite_db_path)
    rows = repo.list_transacoes(mes_ano=mes_ano, categoria=categoria, search=search, limit=limit, offset=offset)
    total = repo.count_transacoes(mes_ano=mes_ano, categoria=categoria, search=search)
    return {"transacoes": rows, "count": len(rows), "total_count": total, "offset": offset, "limit": limit}

@router.put("/api/extratos/transacoes/{transacao_id}")
async def update_transacao_endpoint(transacao_id: int, req: UpdateTransacaoRequest, request: Request):
    """Atualiza dados de uma transação bancária."""
    require_role_action(request, action_type="edit")
    repo = BudgetRepository(settings.sqlite_db_path)
    success = repo.update_transacao(
        transacao_id=transacao_id,
        descricao=req.descricao,
        categoria=req.categoria,
        valor=req.valor,
        data=req.data,
        tipo=req.tipo
    )
    if not success:
        raise HTTPException(status_code=404, detail="Transação não encontrada.")
    return {"status": "ok", "message": "Transação atualizada com sucesso."}

@router.delete("/api/extratos/transacoes/{transacao_id}")
async def delete_transacao_endpoint(transacao_id: int, request: Request):
    """Exclui uma transação bancária."""
    require_role_action(request, action_type="edit")
    repo = BudgetRepository(settings.sqlite_db_path)
    success = repo.delete_transacao(transacao_id)
    if not success:
        raise HTTPException(status_code=404, detail="Transação não encontrada.")
    return {"status": "ok", "message": "Transação excluída com sucesso."}

# ----------------- CRUD DE CATEGORIAS DE DESPESA -----------------

@router.get("/api/categories")
async def list_categories_endpoint(request: Request, active_only: bool = False):
    """Lista as categorias de despesa cadastradas."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autorizado")
    repo = BudgetRepository(settings.sqlite_db_path)
    cats = repo.list_categories(active_only=active_only)
    return {"categories": cats, "count": len(cats)}

@router.post("/api/categories")
async def create_category_endpoint(req: CategoryRequest, request: Request):
    """Cria uma nova categoria de despesa."""
    require_role_action(request, action_type="edit")
    repo = BudgetRepository(settings.sqlite_db_path)
    try:
        cat = repo.create_category(req.name, req.kind)
        return {"status": "ok", "category": cat}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Erro ao criar categoria: {str(e)}")

@router.put("/api/categories/{category_id}")
async def update_category_endpoint(category_id: int, req: CategoryRequest, request: Request):
    """Atualiza uma categoria de despesa existente."""
    require_role_action(request, action_type="edit")
    repo = BudgetRepository(settings.sqlite_db_path)
    success = repo.update_category(category_id, req.name, req.kind, req.active)
    if not success:
        raise HTTPException(status_code=404, detail="Categoria não encontrada.")
    return {"status": "ok", "message": "Categoria atualizada com sucesso."}

@router.delete("/api/categories/{category_id}")
async def delete_category_endpoint(category_id: int, request: Request):
    """Exclui uma categoria de despesa."""
    require_role_action(request, action_type="edit")
    repo = BudgetRepository(settings.sqlite_db_path)
    success = repo.delete_category(category_id)
    if not success:
        raise HTTPException(status_code=404, detail="Categoria não encontrada.")
    return {"status": "ok", "message": "Categoria excluída com sucesso."}

# ----------------- PROCESSAMENTO & UPLOAD DE EXTRATOS -----------------

@router.post("/api/extratos")
async def trigger_extratos_web(request: Request, target_month: Optional[str] = None):
    require_role_action(request, action_type="sync")
    try:
        target_month = validate_target_month(target_month)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    OFXParserService.reload_keywords_from_db()
    resumos = OFXParserService.processar_diretorio_extratos("extratos")

    repo = BudgetRepository(settings.sqlite_db_path)
    todas = [t for r in resumos.values() for t in r.transacoes]
    if todas:
        try:
            repo.persist_transacoes(todas)
        except Exception as e:
            logger.warning("persist_transacoes_failed", error=str(e))

    reconciled_count = repo.reconcile_ofx_to_positions(resumos, target_month=target_month)
    invalidate_canonical_positions_cache(user_id=repo.user_id)

    sheets = AdapterFactory.create_sheets_adapter(settings)
    if hasattr(sheets, "sync_budget_manager"):
        sheets.sync_budget_manager(resumos)
    return {
        "status": "ok",
        "meses": list(resumos.keys()),
        "reconciled_positions": reconciled_count,
        "message": f"Extratos processados. {reconciled_count} ativo(s) conciliado(s) na carteira." if reconciled_count > 0 else "Extratos processados com sucesso."
    }

@router.post("/api/extratos/arquivar")
async def arquivar_extratos_api(request: Request):
    require_role_action(request, action_type="sync")
    movidos = OFXParserService.arquivar_arquivos_processados("extratos", subpasta="processados")
    return {
        "status": "ok",
        "movidos": movidos,
        "message": f"{movidos} arquivo(s) arquivado(s) em 'extratos/processados/'." if movidos > 0 else "Nenhum arquivo pendente para arquivar."
    }

@router.post("/api/upload-extrato")
async def upload_extrato(file: UploadFile = File(...), target_month: Optional[str] = None, request: Request = None):
    require_role_action(request, action_type="sync")
    try:
        target_month = validate_target_month(target_month)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    os.makedirs("extratos", exist_ok=True)
    target_file = os.path.join("extratos", file.filename)
    contents = await file.read()
    with open(target_file, "wb") as f:
        f.write(contents)

    if file.filename.lower().endswith(".zip"):
        import zipfile
        try:
            with zipfile.ZipFile(target_file, "r") as z:
                for fname in z.namelist():
                    if fname.lower().endswith(".ofx"):
                        out_p = os.path.join("extratos", os.path.basename(fname))
                        with open(out_p, "wb") as out_f:
                            out_f.write(z.read(fname))
        except Exception as e:
            logger.warn("zip_extract_error", error=str(e))

    resumos = OFXParserService.processar_diretorio_extratos("extratos")
    repo = BudgetRepository(settings.sqlite_db_path)
    todas = [t for r in resumos.values() for t in r.transacoes]
    if todas:
        try:
            repo.persist_transacoes(todas)
        except Exception as e:
            logger.warning("persist_transacoes_failed", error=str(e))

    reconciled_count = repo.reconcile_ofx_to_positions(resumos, target_month=target_month)
    invalidate_canonical_positions_cache(user_id=repo.user_id)

    sheets = AdapterFactory.create_sheets_adapter(settings)
    if hasattr(sheets, "sync_budget_manager"):
        sheets.sync_budget_manager(resumos)

    return {
        "status": "ok",
        "filename": file.filename,
        "meses": list(resumos.keys()),
        "total_meses": len(resumos),
        "reconciled_positions": reconciled_count
    }

# ----------------- ROTAS DE FECHAMENTO MENSAL & RELATÓRIO PDF -----------------

@router.get("/api/reports/monthly-closing")
async def download_monthly_closing_pdf(request: Request, mes_ano: Optional[str] = None):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autorizado")
    target_month = mes_ano or datetime.now().strftime("%Y-%m")
    repo = BudgetRepository(settings.sqlite_db_path)
    closing_service = MonthlyClosingService(repo)
    try:
        pdf_bytes = closing_service.generate_pdf_report(target_month)
        filename = f"fechamento_barsi_{target_month.replace('-', '_')}.pdf"
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'}
        )
    except Exception as e:
        logger.error("pdf_download_error", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/api/reports/monthly-closing/preview", response_class=HTMLResponse)
async def preview_monthly_closing_html(request: Request, mes_ano: Optional[str] = None):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autorizado")
    target_month = mes_ano or datetime.now().strftime("%Y-%m")
    repo = BudgetRepository(settings.sqlite_db_path)
    closing_service = MonthlyClosingService(repo)
    html = closing_service.generate_html_report(target_month)
    return HTMLResponse(content=html)

# ----------------- ROTAS DE METAS FINANCEIRAS -----------------

@router.get("/api/metas")
async def get_goals_api(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autorizado")
    repo = get_user_budget_repo(request)
    return repo.get_goals_with_progress()

@router.post("/api/metas")
async def create_goal_api(req: GoalCreateRequest, request: Request):
    require_role_action(request, action_type="edit")
    repo = get_user_budget_repo(request)
    try:
        new_id = repo.create_goal(
            tipo=req.tipo,
            titulo=req.titulo,
            valor_alvo=req.valor_alvo,
            ticker=req.ticker,
            prazo_meses=req.prazo_meses,
            notas=req.notas
        )
        return {"status": "ok", "id": new_id}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.put("/api/metas/{goal_id}")
async def update_goal_api(goal_id: int, req: GoalUpdateRequest, request: Request):
    require_role_action(request, action_type="edit")
    repo = get_user_budget_repo(request)
    try:
        ok = repo.update_goal(
            goal_id=goal_id,
            tipo=req.tipo,
            titulo=req.titulo,
            valor_alvo=req.valor_alvo,
            ticker=req.ticker,
            prazo_meses=req.prazo_meses,
            notas=req.notas
        )
        if not ok:
            raise HTTPException(status_code=404, detail="Meta não encontrada")
        return {"status": "ok"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

@router.delete("/api/metas/{goal_id}")
async def delete_goal_api(goal_id: int, request: Request):
    require_role_action(request, action_type="edit")
    repo = get_user_budget_repo(request)
    ok = repo.delete_goal(goal_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Meta não encontrada")
    return {"status": "ok"}

