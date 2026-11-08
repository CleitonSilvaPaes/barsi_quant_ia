"""API HTTP & Painel Web Interativo com Autenticação Criptografada no SQLite e Perfis (RBAC)."""
import os
import time
import urllib.parse
import psutil
from typing import List, Optional, Dict, Any
from pydantic import BaseModel
from fastapi import FastAPI, Depends, HTTPException, Security, Request, Response, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from barsi.config import settings
from barsi.adapters.factory import AdapterFactory
from barsi.adapters.history.user_repository import UserRepository
from barsi.infra.security import create_session_token, decode_session_token
from barsi.services.sync_service import SyncService
from barsi.services.ofx_service import OFXParserService
from barsi.domain.models import RunSummary, Position, SimuladorOrder
from barsi.domain.rules import atualizar_posicao_quant
from barsi.infra.logging import logger

app = FastAPI(title="Barsi Quant IA - Web Dashboard", version="1.3.0")
security = HTTPBearer(auto_error=False)

START_TIME = time.time()
LAST_RUN_SUMMARY: Optional[RunSummary] = None
TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")

# Inicializa o repositório de usuários no banco de dados SQLite
user_repo = UserRepository(settings.sqlite_db_path)
if settings.initial_admin_user and settings.initial_admin_pass:
    user_repo.init_admin_if_empty(settings.initial_admin_user, settings.initial_admin_pass)
elif not user_repo.list_users():
    logger.warning(
        "no_admin_user_configured",
        hint="Defina BARSI_INITIAL_ADMIN_USER e BARSI_INITIAL_ADMIN_PASS no .env ou rode `barsi user-add`"
    )

class EditItem(BaseModel):
    ticker: str
    row_index: int
    div_proj: float
    qtd_antes: Optional[int] = None
    qtd_aporte: Optional[int] = None
    feedback: str = ""

class SaveEditsRequest(BaseModel):
    items: List[EditItem]

def get_sync_service() -> SyncService:
    market = AdapterFactory.create_market_adapter(settings)
    news = AdapterFactory.create_news_adapter(settings)
    ai = AdapterFactory.create_ai_adapter(settings)
    sheets = AdapterFactory.create_sheets_adapter(settings)
    history = AdapterFactory.create_history_adapter(settings)
    notifier = AdapterFactory.create_notification_adapter(settings)
    return SyncService(
        market=market,
        news=news,
        ai=ai,
        sheets=sheets,
        history=history,
        notifier=notifier,
        max_concurrency=settings.max_concurrency
    )

def get_current_user(request: Request) -> Optional[Dict[str, Any]]:
    """Extrai e valida o token de sessão criptografado via HMAC-SHA256."""
    token = request.cookies.get("barsi_session")
    if not token:
        return None
    return decode_session_token(token, settings.secret_key)

def require_role_action(request: Request, action_type: str = "edit") -> Dict[str, Any]:
    """Valida se o usuário autenticado possui o perfil adequado (RBAC)."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autenticado. Faça login primeiro.")
    if user.get("role") == "leitor" and action_type in ["edit", "run", "sync"]:
        raise HTTPException(status_code=403, detail="Perfil de Leitor: Apenas visualização permitida.")
    return user

@app.get("/login", response_class=HTMLResponse)
async def login_page(error: Optional[str] = None):
    login_path = os.path.join(TEMPLATES_DIR, "login.html")
    with open(login_path, "r", encoding="utf-8") as f:
        html = f.read()
    err_html = f'<div class="err">{error}</div>' if error else ""
    return html.replace("{{ERROR_MSG}}", err_html)

@app.post("/login")
async def do_login(request: Request):
    body = (await request.body()).decode("utf-8", errors="ignore")
    params = urllib.parse.parse_qs(body)
    username = params.get("username", [""])[0].strip()
    password = params.get("password", [""])[0].strip()

    user = user_repo.authenticate(username, password)
    if user:
        token = create_session_token(user["id"], user["username"], user["role"], settings.secret_key)
        res = RedirectResponse(url="/", status_code=303)
        res.set_cookie(key="barsi_session", value=token, httponly=True, max_age=86400 * 7, samesite="lax")
        return res

    return RedirectResponse(url="/login?error=Usuário+ou+senha+incorretos", status_code=303)

@app.get("/logout")
async def do_logout():
    res = RedirectResponse(url="/login", status_code=303)
    res.delete_cookie(key="barsi_session")
    return res

@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login")

    mem = psutil.virtual_memory()
    proc = psutil.Process()
    uptime_sec = int(time.time() - START_TIME)
    uptime_str = f"{uptime_sec // 3600}h {(uptime_sec % 3600) // 60}m {uptime_sec % 60}s"
    free_mb = round(mem.available / (1024 * 1024), 1)
    total_mb = round(mem.total / (1024 * 1024), 1)
    rss_mb = round(proc.memory_info().rss / (1024 * 1024), 1)

    index_path = os.path.join(TEMPLATES_DIR, "index.html")
    with open(index_path, "r", encoding="utf-8") as f:
        html = f.read()

    role_badge = f"{user['role'].upper()}"
    html = (
        html.replace("{{FREE_MB}}", str(free_mb))
            .replace("{{TOTAL_MB}}", str(total_mb))
            .replace("{{RSS_MB}}", str(rss_mb))
            .replace("{{UPTIME_STR}}", uptime_str)
            .replace("{{AI_PROVIDER}}", settings.ai_provider.upper())
            .replace("{{USER_NAME}}", f"{user['user']} ({role_badge})")
    )
    return HTMLResponse(content=html)

@app.get("/api/carteira")
async def get_carteira(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autorizado")
    
    sheets = AdapterFactory.create_sheets_adapter(settings)
    positions = await sheets.load_positions()
    
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
            "valor_total": round(p.valor_total, 2),
            "total_aporte": round(p.total_aporte, 2),
            "status": p.status_barsi.value,
            "div_proj": round(p.div_proj, 2),
            "feedback": p.feedback_usuario,
            "is_readonly": is_leitor
        })
    return JSONResponse(content=data)

@app.get("/api/simulador")
async def get_simulador(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autorizado")
    
    sheets = AdapterFactory.create_sheets_adapter(settings)
    if hasattr(sheets, "load_simulador_orders"):
        orders = await sheets.load_simulador_orders()
        return JSONResponse(content=[o.model_dump() for o in orders])
    return JSONResponse(content=[])

@app.post("/api/carteira")
async def save_carteira_edits(req: SaveEditsRequest, request: Request):
    require_role_action(request, action_type="edit")
    
    sheets = AdapterFactory.create_sheets_adapter(settings)
    positions = await sheets.load_positions()
    pos_map = {p.ticker: p for p in positions}

    for item in req.items:
        if item.ticker in pos_map:
            p = pos_map[item.ticker]
            p.div_proj = item.div_proj
            if item.qtd_antes is not None:
                p.qtd_antes = max(0, item.qtd_antes)
            if item.qtd_aporte is not None:
                p.qtd_aporte = max(0, item.qtd_aporte)
            p.qtd_total = p.qtd_antes + p.qtd_aporte
            p.valor_total = round(p.qtd_total * p.preco_atual, 2)
            p.total_aporte = round(p.qtd_aporte * p.preco_atual, 2)
            p.feedback_usuario = item.feedback
            atualizar_posicao_quant(p, p.preco_atual, p.div_proj)

    await sheets.update_positions(positions)
    return {"status": "ok", "updated_count": len(req.items)}

@app.post("/api/run")
async def trigger_run_web(request: Request):
    require_role_action(request, action_type="run")
    global LAST_RUN_SUMMARY
    service = get_sync_service()
    summary = await service.execute_run()
    LAST_RUN_SUMMARY = summary
    return summary.model_dump()

@app.post("/api/extratos")
async def trigger_extratos_web(request: Request):
    require_role_action(request, action_type="sync")
    OFXParserService.reload_keywords_from_db()
    resumos = OFXParserService.processar_diretorio_extratos("extratos")
    sheets = AdapterFactory.create_sheets_adapter(settings)
    if hasattr(sheets, "sync_budget_manager"):
        sheets.sync_budget_manager(resumos)
    return {"status": "ok", "meses": list(resumos.keys())}

@app.post("/api/upload-extrato")
async def upload_extrato(file: UploadFile = File(...), request: Request = None):
    require_role_action(request, action_type="sync")
    os.makedirs("extratos", exist_ok=True)
    target_file = os.path.join("extratos", file.filename)
    contents = await file.read()
    with open(target_file, "wb") as f:
        f.write(contents)

    # Se for arquivo .zip, extrai arquivos .ofx
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

    # Executa a conciliação automática e atualiza o orçamento
    resumos = OFXParserService.processar_diretorio_extratos("extratos")
    sheets = AdapterFactory.create_sheets_adapter(settings)
    if hasattr(sheets, "sync_budget_manager"):
        sheets.sync_budget_manager(resumos)

    return {
        "status": "ok",
        "filename": file.filename,
        "meses": list(resumos.keys()),
        "total_meses": len(resumos)
    }

@app.post("/api/sync-tvbox")
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

@app.get("/health")
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
