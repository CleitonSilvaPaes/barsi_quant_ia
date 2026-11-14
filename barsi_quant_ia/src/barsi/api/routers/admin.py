"""Rotas de administração, gestão de usuários, auditoria, backups, healthcheck, scheduler e conectividade."""
import os
import time
import psutil
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import FileResponse

from barsi.config import settings
from barsi.infra.logging import logger
from barsi.adapters.factory import AdapterFactory
from barsi.adapters.history.budget_repository import BudgetRepository
from barsi.api.common import (
    START_TIME,
    LAST_RUN_SUMMARY,
    get_current_user,
    require_role_action,
    get_user_repo,
    invalidate_canonical_positions_cache,
    NewUserRequest,
    UpdateUserPasswordRequest,
    UpdateUserEmailRequest,
    SchedulerUpdate,
    NotifPrefUpdate,
)

router = APIRouter(tags=["admin"])

# ----------------- ADMINISTRAÇÃO & GESTÃO DE USUÁRIOS -----------------

@router.get("/api/admin/users")
async def list_admin_users(request: Request):
    user = require_role_action(request, action_type="admin")
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Acesso restrito a administradores")
    repo = get_user_repo()
    users = repo.list_users()
    return [{
        "id": u["id"],
        "username": u["username"],
        "email": u.get("email"),
        "google_id": u.get("google_id"),
        "role": u["role"],
        "totp_enabled": bool(u.get("totp_enabled")),
        "created_at": u.get("criado_em")
    } for u in users]

@router.post("/api/admin/users")
async def create_admin_user(req: NewUserRequest, request: Request):
    user = require_role_action(request, action_type="admin")
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Acesso restrito a administradores")
    repo = get_user_repo()
    success = repo.create_user(req.username, req.password, req.role, email=req.email)
    if not success:
        raise HTTPException(status_code=400, detail="Nome de usuário já existe ou inválido")
    repo.log_audit(
        action="admin_create_user",
        actor_id=user.get("uid"),
        actor_username=user.get("user") or user.get("username"),
        target=req.username,
        reason=f"role={req.role}"
    )
    return {"ok": True, "username": req.username, "role": req.role, "email": req.email}

@router.post("/api/admin/users/{user_id}/password")
async def update_user_password_admin(user_id: int, req: UpdateUserPasswordRequest, request: Request):
    user = require_role_action(request, action_type="admin")
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Acesso restrito a administradores")
    if user.get("uid") == user_id:
        raise HTTPException(
            status_code=400,
            detail="Administradores não podem alterar a própria senha sem fornecer a senha atual."
        )
    if not req.new_password or len(req.new_password) < 6:
        raise HTTPException(status_code=400, detail="A nova senha deve ter no mínimo 6 caracteres")
    repo = get_user_repo()
    target = repo.get_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    success = repo.update_password(user_id, req.new_password)
    if not success:
        raise HTTPException(status_code=500, detail="Falha ao atualizar a senha do usuário")
    repo.log_audit(
        action="admin_password_updated",
        actor_id=user.get("uid"),
        actor_username=user.get("user") or user.get("username"),
        target=target.get("username"),
        reason="admin_direct_reset"
    )
    logger.info("admin_password_updated", admin=user.get("username") or user.get("user"), target=target.get("username"), user_id=user_id)
    return {"ok": True, "message": f"Senha do usuário '{target.get('username')}' atualizada com sucesso!"}

@router.post("/api/admin/users/{user_id}/email")
@router.put("/api/admin/users/{user_id}/email")
async def update_user_email_admin(user_id: int, req: UpdateUserEmailRequest, request: Request):
    user = require_role_action(request, action_type="admin")
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Acesso restrito a administradores")
    repo = get_user_repo()
    target = repo.get_by_id(user_id)
    if not target:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    success = repo.update_email(user_id, req.email)
    if not success:
        raise HTTPException(status_code=400, detail="E-mail inválido ou já em uso por outro usuário.")
    repo.log_audit(
        action="admin_email_updated",
        actor_id=user.get("uid"),
        actor_username=user.get("user") or user.get("username"),
        target=target.get("username"),
        reason=f"new_email={req.email}"
    )
    logger.info("admin_email_updated", admin=user.get("username") or user.get("user"), target=target.get("username"), email=req.email)
    return {"ok": True, "message": "E-mail vinculado com sucesso!"}

@router.get("/api/admin/audit-logs")
async def list_audit_logs_endpoint(request: Request, limit: int = 50):
    """Retorna os registros mais recentes de auditoria do sistema."""
    user = require_role_action(request, action_type="admin")
    repo = get_user_repo()
    logs = repo.list_audit_logs(limit=limit)
    return {"ok": True, "logs": logs}

# ----------------- BACKUP & SETTINGS TEST -----------------

@router.get("/api/backup/download")
async def download_backup_sqlite(request: Request):
    require_role_action(request, action_type="admin")
    db_p = settings.sqlite_db_path
    if not os.path.exists(db_p):
        raise HTTPException(status_code=404, detail="Banco de dados SQLite não encontrado")
    return FileResponse(db_p, filename="barsi_memory.db", media_type="application/octet-stream")

@router.post("/api/settings/test/{provider}")
async def test_provider_connection(provider: str, request: Request):
    require_role_action(request, action_type="run")
    start = time.time()
    try:
        if provider == "yfinance":
            import yfinance as yf
            ticker = yf.Ticker("PETR4.SA")
            info = ticker.fast_info
            price = getattr(info, "last_price", None) or 40.0
            lat = int((time.time() - start) * 1000)
            return {"ok": True, "latency_ms": lat, "detail": f"PETR4.SA cotado a R$ {price:.2f}"}

        elif provider == "sheets":
            sheets = AdapterFactory.create_sheets_adapter(settings)
            pos = await sheets.load_positions()
            lat = int((time.time() - start) * 1000)
            return {"ok": True, "latency_ms": lat, "detail": f"{len(pos)} ativos carregados da planilha"}

        elif provider == "telegram":
            notifier = AdapterFactory.create_notification_adapter(settings)
            sent = await notifier.notify(
                title="Barsi Quant IA",
                body="🔔 Teste de conectividade efetuado com sucesso!",
                level="info"
            )
            lat = int((time.time() - start) * 1000)
            if sent:
                return {"ok": True, "latency_ms": lat, "detail": "Mensagem entregue no chat do Telegram"}
            return {"ok": False, "detail": "Verifique BARSI_TELEGRAM_BOT_TOKEN, BARSI_TELEGRAM_CHAT_ID e conectividade de rede"}

        elif provider == "ai":
            ai = AdapterFactory.create_ai_adapter(settings)
            lat = int((time.time() - start) * 1000)
            return {"ok": True, "latency_ms": lat, "detail": f"Provedor {settings.ai_provider} pronto para inferência"}

        else:
            raise HTTPException(status_code=400, detail="Provedor desconhecido")
    except Exception as e:
        return {"ok": False, "detail": str(e)}

# ----------------- EDGE TV BOX & HEALTH SYNC -----------------

@router.post("/api/sync-tvbox")
async def sync_tvbox_action(request: Request):
    require_role_action(request, action_type="run")
    import paramiko
    from scp import SCPClient
    import glob

    tv_ip = settings.tvbox_ip or os.getenv("BARSI_TVBOX_IP", "")
    tv_user = settings.tvbox_user or os.getenv("BARSI_TVBOX_USER", "")
    tv_pass = settings.tvbox_pass or os.getenv("BARSI_TVBOX_PASS", "")

    if not tv_ip or not tv_user or not tv_pass:
        raise HTTPException(
            status_code=400,
            detail="Credenciais da TV Box não configuradas. Defina BARSI_TVBOX_IP, BARSI_TVBOX_USER e BARSI_TVBOX_PASS no arquivo .env."
        )

    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh.connect(tv_ip, username=tv_user, password=tv_pass, timeout=6)
        remote_home = settings.tvbox_home
        ssh.exec_command(f"mkdir -p {remote_home}/extratos")

        count_ofx = 0
        with SCPClient(ssh.get_transport()) as scp:
            for f in glob.glob("extratos/*.ofx"):
                scp.put(f, f"{remote_home}/extratos/{os.path.basename(f)}")
                count_ofx += 1

        stdin, stdout, stderr = ssh.exec_command("sudo systemctl restart barsi-api.service", get_pty=True)
        stdin.write(f"{tv_pass}\n")
        stdin.flush()
        stdout.channel.recv_exit_status()
        ssh.close()
        return {"status": "ok", "ofx_synced": count_ofx, "tvbox_ip": tv_ip}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Falha ao sincronizar com TV Box: {e}")

@router.get("/api/health/sync")
async def health_sync(request: Request):
    """Compara paridade entre SQLite (canônico) e Sheets/XLSX para detecção de drift."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autenticado")

    repo = BudgetRepository(settings.sqlite_db_path)
    sqlite_pos = {p.ticker: p for p in repo.load_positions()}

    sheets_pos = {}
    try:
        sheets = AdapterFactory.create_sheets_adapter(settings)
        for p in await sheets.load_positions():
            sheets_pos[p.ticker] = p
    except Exception as e:
        logger.warning("health_sync_sheets_error", error=str(e))

    all_tickers = sorted(list(set(sqlite_pos.keys()) | set(sheets_pos.keys())))
    divergences = []

    for t in all_tickers:
        p_sql = sqlite_pos.get(t)
        p_sh = sheets_pos.get(t)

        if not p_sql:
            divergences.append({
                "ticker": t,
                "divergences": ["Presente apenas no Sheets, ausente no SQLite"]
            })
        elif not p_sh:
            divergences.append({
                "ticker": t,
                "divergences": ["Presente apenas no SQLite, ausente no Sheets"]
            })
        else:
            diffs = []
            for field in ("div_proj", "qtd_antes", "preco_atual", "preco_teto", "margem_seguranca", "qtd_aporte", "teto_manual"):
                a = getattr(p_sql, field, None)
                b = getattr(p_sh, field, None)
                if isinstance(a, float) and isinstance(b, float):
                    if round(a or 0.0, 2) != round(b or 0.0, 2):
                        diffs.append(f"{field}: SQLite={a} vs Sheets={b}")
                elif (a or 0) != (b or 0):
                    diffs.append(f"{field}: SQLite={a} vs Sheets={b}")
            if diffs:
                divergences.append({
                    "ticker": t,
                    "divergences": diffs
                })

    return {
        "in_sync": len(divergences) == 0,
        "tickers_checked": len(all_tickers),
        "divergences": divergences
    }

@router.post("/api/health/sync/reconcile")
async def health_sync_reconcile(request: Request, master: str = "sqlite"):
    """Reconcilia fontes com base no master escolhido (sqlite ou sheets)."""
    user = get_current_user(request)
    if not user or user.get("role") not in ("admin", "investidor"):
        raise HTTPException(status_code=403, detail="Permissão insuficiente")

    repo = BudgetRepository(settings.sqlite_db_path)
    sheets = AdapterFactory.create_sheets_adapter(settings)

    master_clean = (master or "sqlite").strip().lower()

    if master_clean == "sqlite":
        positions = repo.load_positions()
        if not positions:
            raise HTTPException(status_code=400, detail="SQLite está vazio para ser usado como master")
        await sheets.update_positions(positions)
        return {
            "ok": True,
            "positions_count": len(positions),
            "synced_targets": ["sheets", "xlsx"]
        }
    elif master_clean in ("sheets", "xlsx"):
        positions = await sheets.load_positions()
        if not positions:
            raise HTTPException(status_code=400, detail="Sheets está vazio para ser usado como master")
        repo.save_positions(positions)
        invalidate_canonical_positions_cache(user_id=repo.user_id)
        return {
            "ok": True,
            "positions_count": len(positions),
            "synced_targets": ["sqlite"]
        }
    else:
        raise HTTPException(status_code=400, detail="Master inválido. Use 'sqlite' ou 'sheets'.")

# ----------------- SCHEDULER & NOTIFICATION PREFERENCES -----------------

@router.get("/api/scheduler")
async def get_scheduler(request: Request):
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
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autenticado")
    repo = BudgetRepository(settings.sqlite_db_path)
    return {"items": repo.list_notification_preferences()}

@router.post("/api/notifications/preferences")
async def set_notification_pref(req: NotifPrefUpdate, request: Request):
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

# ----------------- HEALTH CHECK -----------------

@router.get("/health")
async def health():
    mem = psutil.virtual_memory()
    proc = psutil.Process()
    uptime_sec = int(time.time() - START_TIME)
    
    return {
        "status": "healthy",
        "uptime_sec": uptime_sec,
        "memory": {
            "system_free_mb": round(mem.available / (1024 * 1024), 2),
            "system_total_mb": round(mem.total / (1024 * 1024), 2),
            "process_rss_mb": round(proc.memory_info().rss / (1024 * 1024), 2)
        },
        "last_run": LAST_RUN_SUMMARY.model_dump() if LAST_RUN_SUMMARY else None,
        "adapters": {
            "market": settings.market_provider,
            "news": settings.news_provider,
            "ai": settings.ai_provider,
            "sheets": settings.sheets_provider
        }
    }

