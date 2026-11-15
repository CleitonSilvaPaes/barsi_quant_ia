"""Rotas de configuração do agendador automático (Cron/Interval) e preferências de notificações."""
from fastapi import APIRouter, Request, HTTPException

from barsi.config import settings
from barsi.adapters.history.budget_repository import BudgetRepository
from barsi.api.common import (
    get_current_user,
    require_role_action,
    SchedulerUpdate,
    NotifPrefUpdate,
)

router = APIRouter(tags=["scheduler"])


@router.get("/api/scheduler")
async def get_scheduler(request: Request):
    """Consulta os parâmetros vigentes do scheduler automatizado de sincronização."""
    require_role_action(request, action_type="admin")
    repo = BudgetRepository(settings.sqlite_db_path)
    cfg = repo.get_scheduler_config()
    return {
        "enabled": bool(cfg["enabled"]),
        "days_of_week": cfg["days_of_week"],
        "hours": cfg["hours"],
        "minute": cfg["minute"],
        "updated_at": cfg["updated_at"],
        "updated_by": cfg["updated_by"],
        "next_run": repo.next_scheduled_run(),
    }


@router.post("/api/scheduler")
async def update_scheduler(req: SchedulerUpdate, request: Request):
    """Atualiza a cadência e ativação do scheduler com auditoria de usuário."""
    user = require_role_action(request, action_type="admin")
    repo = BudgetRepository(settings.sqlite_db_path)
    try:
        user_name = user.get("user") or user.get("username", "admin")
        cfg = repo.update_scheduler_config(
            **req.model_dump(exclude_none=True),
            updated_by=user_name,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "ok": True,
        "config": {
            "enabled": bool(cfg["enabled"]),
            "days_of_week": cfg["days_of_week"],
            "hours": cfg["hours"],
            "minute": cfg["minute"],
        },
        "next_run": repo.next_scheduled_run(),
    }


@router.get("/api/notifications/preferences")
async def get_notification_prefs(request: Request):
    """Lista as preferências globais de notificação e canais por tipo de alerta."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autenticado")
    repo = BudgetRepository(settings.sqlite_db_path)
    return {"items": repo.list_notification_preferences()}


@router.post("/api/notifications/preferences")
async def set_notification_pref(req: NotifPrefUpdate, request: Request):
    """Habilita ou desabilita o disparo de alertas específicos ou em lote."""
    user = require_role_action(request, action_type="admin")
    repo = BudgetRepository(settings.sqlite_db_path)
    user_name = user.get("user") or user.get("username", "admin")
    if req.alert_type == "all":
        count = repo.set_all_notifications_enabled(req.enabled, updated_by=user_name)
        return {"ok": True, "alert_type": "all", "enabled": req.enabled, "updated": count}
    try:
        repo.set_notification_enabled(req.alert_type, req.enabled, updated_by=user_name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "alert_type": req.alert_type, "enabled": req.enabled}
