"""Rotas de IA Cognitiva: Insights, Validação Humana (RLHF), Chain-of-Thought e Early Warning NLP."""
import os
import sqlite3
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse

from barsi.config import settings
from barsi.adapters.factory import AdapterFactory
from barsi.adapters.history.budget_repository import BudgetRepository
from barsi.domain.rules import atualizar_posicao_quant
from barsi.services.early_warning_service import EarlyWarningService
from barsi.api.common import (
    get_current_user,
    require_role_action,
    get_canonical_positions,
    FeedbackRequest,
    FeedbackBatchRequest,
    FeedbackBatchItem,
)

router = APIRouter(tags=["insights"])


@router.get("/api/insights")
async def get_insights_api(request: Request):
    """Retorna os insights cognitivos gerados pela IA combinados com histórico em memória e taxa RLHF."""
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
    """Registra em lote o feedback humano de concordância/discordância (RLHF)."""
    require_role_action(request, action_type="edit")
    if not req.items:
        return {"ok": True, "count": 0}

    budget_repo = BudgetRepository(settings.sqlite_db_path)
    for it in req.items:
        budget_repo.update_insight_feedback(it.ticker, it.feedback)

    return {"ok": True, "count": len(req.items)}


@router.post("/api/insights/feedback")
async def save_insight_feedback(req: FeedbackRequest, request: Request):
    """Registra o feedback pontual de um único ativo (compatibilidade retroativa)."""
    batch_req = FeedbackBatchRequest(items=[FeedbackBatchItem(ticker=req.ticker, feedback=req.feedback)])
    res = await save_insight_feedback_batch(batch_req, request)
    return {
        "ok": res.get("ok", True),
        "ticker": req.ticker,
        "feedback": req.feedback
    }


@router.get("/api/early-warning/alerts")
async def list_early_warning_alerts(request: Request, active_only: bool = True):
    """Lista alertas de risco e ruptura de tese identificados pelo Early Warning NLP."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autorizado")
    repo = BudgetRepository(settings.sqlite_db_path)
    news = AdapterFactory.create_news_adapter(settings)
    service = EarlyWarningService(repo, news)
    return JSONResponse(content=service.list_alerts(active_only=active_only))


@router.post("/api/early-warning/scan")
async def scan_early_warning_portfolio(request: Request):
    """Executa varredura ativa de notícias e fatos relevantes para todos os ativos em carteira."""
    require_role_action(request, action_type="edit")
    repo = BudgetRepository(settings.sqlite_db_path)
    news = AdapterFactory.create_news_adapter(settings)
    notifier = AdapterFactory.create_notification_adapter(settings)
    service = EarlyWarningService(repo, news, notifier)
    alerts = await service.scan_portfolio()
    return JSONResponse(content={"status": "ok", "alerts_count": len(alerts), "alerts": alerts})


@router.post("/api/early-warning/{alert_id}/dismiss")
async def dismiss_early_warning_alert(alert_id: int, request: Request):
    """Dispensa e arquiva um alerta de risco já revisado pelo usuário."""
    require_role_action(request, action_type="edit")
    repo = BudgetRepository(settings.sqlite_db_path)
    news = AdapterFactory.create_news_adapter(settings)
    service = EarlyWarningService(repo, news)
    ok = service.dismiss_alert(alert_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Alerta não encontrado")
    return JSONResponse(content={"status": "ok"})
