"""API HTTP & Painel Web Interativo com Sidebar, Roteamento Modular e Perfis (RBAC)."""
import os
import time
import json
import asyncio
import urllib.request
import urllib.parse
from datetime import datetime
import io
import sqlite3
import pandas as pd
from typing import List, Optional, Dict, Any
from contextlib import asynccontextmanager
import psutil
from pydantic import BaseModel, Field
from fastapi import FastAPI, Depends, HTTPException, Security, Request, Response, UploadFile, File, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse, StreamingResponse
from fastapi.security import HTTPBearer
from fastapi.staticfiles import StaticFiles

from barsi.config import settings
from barsi.adapters.factory import AdapterFactory
from barsi.adapters.history.user_repository import UserRepository
from barsi.adapters.history.budget_repository import BudgetRepository, validate_target_month
from barsi.infra.security import (
    create_session_token,
    decode_session_token,
    generate_csrf_token,
    verify_csrf_token,
)
from barsi.services.sync_service import SyncService
from barsi.services.ofx_service import OFXParserService
from barsi.domain.models import RunSummary, Position, SimuladorOrder
from barsi.domain.rules import atualizar_posicao_quant
from barsi.infra.logging import logger

@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.telegram_bot_token and not (settings.telegram_webhook_secret or "").strip():
        logger.warning(
            "telegram_webhook_secret_missing",
            hint="Configure BARSI_TELEGRAM_WEBHOOK_SECRET e secret_token no setWebhook para proteção extra contra spoofing."
        )
    yield

app = FastAPI(title="Barsi Quant IA - Web Suite", version="2.0.0", lifespan=lifespan)
security = HTTPBearer(auto_error=False)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

CSRF_EXEMPT_PATHS = {
    "/login",
    "/api/telegram/webhook",
    "/docs",
    "/openapi.json",
    "/redoc",
}

@app.middleware("http")
async def csrf_protection_middleware(request: Request, call_next):
    """Valida tokens CSRF em requisições de alteração de estado autenticadas via cookies."""
    if settings.csrf_enabled and request.method in ("POST", "PUT", "DELETE", "PATCH"):
        path = request.url.path
        if path not in CSRF_EXEMPT_PATHS:
            session_token = request.cookies.get("barsi_session")
            if session_token:
                csrf_token = (
                    request.headers.get("X-CSRF-Token")
                    or request.headers.get("X-CSRFToken")
                    or request.query_params.get("csrf_token")
                )
                if not csrf_token or not verify_csrf_token(str(csrf_token), session_token, settings.secret_key):
                    logger.warning("csrf_validation_failed", path=path, method=request.method)
                    return JSONResponse(
                        status_code=403,
                        content={"detail": "Falha na validação CSRF: Token CSRF ausente ou inválido."}
                    )

    response = await call_next(request)

    session_token = request.cookies.get("barsi_session")
    if session_token and not request.url.path.startswith("/static"):
        token_csrf = generate_csrf_token(session_token, settings.secret_key)
        response.set_cookie(
            key="barsi_csrf",
            value=token_csrf,
            httponly=False,
            samesite="lax",
            path="/"
        )
    return response

@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline'; "
        "font-src 'self' data:; "
        "img-src 'self' data: https:; "
        "connect-src 'self'; "
        "frame-ancestors 'none';"
    )
    return response

START_TIME = time.time()
LAST_RUN_SUMMARY: Optional[RunSummary] = None
TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")

# Inicializa o repositório de usuários de forma preguiçosa (Lazy Factory) para garantir isolamento de testes
_user_repo_cache: Optional[UserRepository] = None

def get_user_repo() -> UserRepository:
    global _user_repo_cache
    if _user_repo_cache is None:
        _user_repo_cache = UserRepository(settings.sqlite_db_path)
        if settings.initial_admin_user and settings.initial_admin_pass:
            _user_repo_cache.init_admin_if_empty(settings.initial_admin_user, settings.initial_admin_pass)
        elif not _user_repo_cache.list_users():
            logger.warning(
                "no_admin_user_configured",
                hint="Defina BARSI_INITIAL_ADMIN_USER e BARSI_INITIAL_ADMIN_PASS no .env ou rode `barsi user-add`"
            )
    return _user_repo_cache

class EditItem(BaseModel):
    ticker: str
    row_index: int
    div_proj: float
    preco_atual: Optional[float] = Field(default=None, ge=0)
    qtd_antes: Optional[int] = Field(default=None, ge=0)
    qtd_aporte: Optional[int] = Field(default=None, ge=0)
    preco_teto: Optional[float] = Field(default=None, ge=0)
    teto_manual: Optional[bool] = None
    custo_medio: Optional[float] = Field(default=None, ge=0)
    feedback: str = ""

class DividendItem(BaseModel):
    ticker: str
    data_pagamento: str
    valor_total: float = Field(..., gt=0)
    valor_por_cota: Optional[float] = Field(default=0.0, ge=0)
    quantidade: Optional[int] = Field(default=0, ge=0)
    tipo: Optional[str] = "DIVIDENDO"
    fonte: Optional[str] = "manual"

class TotpConfirmRequest(BaseModel):
    code: str

class SchedulerUpdate(BaseModel):
    enabled: Optional[bool] = None
    days_of_week: Optional[str] = None
    hours: Optional[str] = None
    minute: Optional[int] = Field(default=None, ge=0, le=59)

class NotifPrefUpdate(BaseModel):
    alert_type: str       # "margem_20" | "radar_summary" | "all"
    enabled: bool

class SaveEditsRequest(BaseModel):
    items: List[EditItem]

class FeedbackRequest(BaseModel):
    ticker: str
    feedback: str

class FeedbackBatchItem(BaseModel):
    ticker: str
    feedback: str

class FeedbackBatchRequest(BaseModel):
    items: List[FeedbackBatchItem]

class NewUserRequest(BaseModel):
    username: str
    password: str
    role: str = "leitor"

class ArtifactCreateRequest(BaseModel):
    name: str
    description: Optional[str] = ""
    chart_type: str = "bar"
    data_source: str
    fields: List[str]
    filters: Optional[Dict[str, Any]] = None
    visible_in: Optional[List[str]] = Field(default_factory=lambda: ["analytics"])

def get_sync_service() -> SyncService:
    market = AdapterFactory.create_market_adapter(settings)
    news = AdapterFactory.create_news_adapter(settings)
    ai = AdapterFactory.create_ai_adapter(settings)
    sheets = AdapterFactory.create_sheets_adapter(settings)
    history = AdapterFactory.create_history_adapter(settings)
    notifier = AdapterFactory.create_notification_adapter(settings)
    budget_repo = BudgetRepository(settings.sqlite_db_path)
    return SyncService(
        market=market,
        news=news,
        ai=ai,
        sheets=sheets,
        history=history,
        notifier=notifier,
        budget_repo=budget_repo,
        max_concurrency=settings.max_concurrency
    )

async def get_canonical_positions() -> List[Position]:
    """Obtém as posições canônicas do SQLite (fonte da verdade).
    Caso o SQLite esteja vazio (primeiro boot ou banco novo), busca seed do Google Sheets."""
    budget_repo = BudgetRepository(settings.sqlite_db_path)
    positions = budget_repo.load_positions()
    if not positions:
        try:
            sheets = AdapterFactory.create_sheets_adapter(settings)
            positions = await sheets.load_positions()
            if positions:
                budget_repo.save_positions(positions)
                logger.info("sqlite_seeded_from_sheets", count=len(positions))
        except Exception as e:
            logger.warning("sheets_initial_seed_failed", error=str(e))
    return positions

def get_current_user(request: Request) -> Optional[Dict[str, Any]]:
    """Extrai e valida o token de sessão criptografado via HMAC-SHA256."""
    token = request.cookies.get("barsi_session")
    if not token:
        return None
    return decode_session_token(token, settings.secret_key)

def require_role_action(request: Request, action_type: str = "edit") -> Dict[str, Any]:
    """RBAC centralizado. Valida permissões pelo papel do usuário sem regras dispersas."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autenticado. Faça login primeiro.")
    
    role = user.get("role", "leitor").lower()
    allowed_by_action = {
        "read":  {"admin", "investidor", "leitor"},
        "edit":  {"admin", "investidor"},
        "run":   {"admin", "investidor"},
        "sync":  {"admin", "investidor"},
        "admin": {"admin"},
    }
    allowed_roles = allowed_by_action.get(action_type, {"admin"})
    if role not in allowed_roles:
        raise HTTPException(
            status_code=403,
            detail=f"Perfil '{role}' não possui permissão para a ação '{action_type}'."
        )
    return user

def render_page(template_name: str, request: Request, page_title: str, active_nav: str) -> Response:
    """Renderiza páginas compartilhando o layout mestre base.html com sidebar e telemetria."""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login")

    mem = psutil.virtual_memory()
    proc = psutil.Process()
    uptime_sec = int(time.time() - START_TIME)
    uptime_str = f"{uptime_sec // 3600}h {(uptime_sec % 3600) // 60}m"
    free_mb = round(mem.available / (1024 * 1024), 1)
    total_mb = round(mem.total / (1024 * 1024), 1)
    rss_mb = round(proc.memory_info().rss / (1024 * 1024), 1)

    base_path = os.path.join(TEMPLATES_DIR, "base.html")
    with open(base_path, "r", encoding="utf-8") as f:
        base_html = f.read()

    page_path = os.path.join(TEMPLATES_DIR, f"{template_name}.html")
    if os.path.exists(page_path):
        with open(page_path, "r", encoding="utf-8") as f:
            content_html = f.read()
    else:
        content_html = f"<div class='card'>Página {template_name} em desenvolvimento.</div>"

    role_badge = f"{user['role'].upper()}"
    navs = ["home", "carteira", "aportes", "budget", "extratos", "insights", "analytics", "settings", "admin", "help"]

    is_admin = (user.get("role") == "admin")
    admin_nav_html = f'<a href="/admin" class="nav-item {"active" if active_nav == "admin" else ""}"><span class="icon">🔐</span> Admin & Usuários</a>' if is_admin else ""

    session_token = request.cookies.get("barsi_session", "")
    csrf_token = generate_csrf_token(session_token, settings.secret_key)

    html = (
        base_html.replace("{{TITLE}}", page_title)
                 .replace("{{PAGE_HEADING}}", page_title)
                 .replace("{{PAGE_CONTENT}}", content_html)
                 .replace("{{CSRF_TOKEN}}", csrf_token)
                 .replace("{{FREE_MB}}", str(free_mb))
                 .replace("{{TOTAL_MB}}", str(total_mb))
                 .replace("{{RSS_MB}}", str(rss_mb))
                 .replace("{{UPTIME_STR}}", uptime_str)
                 .replace("{{AI_PROVIDER}}", settings.ai_provider.upper())
                 .replace("{{USER_NAME}}", f"{user['user']} ({role_badge})")
                 .replace("{{NAV_ADMIN_ITEM}}", admin_nav_html)
    )
    for n in navs:
        html = html.replace(f"{{{{NAV_{n.upper()}}}}}", "active" if n == active_nav else "")

    res = HTMLResponse(content=html)
    res.set_cookie(key="barsi_csrf", value=csrf_token, httponly=False, samesite="lax", path="/")
    return res

# ----------------- ROTAS DE AUTENTICAÇÃO & RATE LIMITING -----------------
_login_attempts: Dict[str, List[float]] = {}
LOGIN_RATE_LIMIT_MAX = 5
LOGIN_RATE_LIMIT_WINDOW = 60.0  # Janela de 60 segundos

def _get_client_ip(request: Request) -> str:
    """Extrai IP real considerando proxies reversos (Cloudflare Tunnel, Nginx, etc.).
    Só confia em headers de proxy se a conexão originar de loopback ou testclient.
    """
    real_ip = request.client.host if request.client else "127.0.0.1"
    if real_ip in ("127.0.0.1", "::1", "localhost", "testclient"):
        cf_ip = request.headers.get("CF-Connecting-IP")
        if cf_ip:
            return cf_ip.strip()
        xff = request.headers.get("X-Forwarded-For", "")
        if xff:
            return xff.split(",")[0].strip()
    return real_ip

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: Optional[str] = None):
    login_path = os.path.join(TEMPLATES_DIR, "login.html")
    with open(login_path, "r", encoding="utf-8") as f:
        html = f.read()
    err_html = f'<div class="err">{error}</div>' if error else ""
    session_token = request.cookies.get("barsi_session", "")
    csrf_token = generate_csrf_token(session_token, settings.secret_key)
    html = html.replace("{{ERROR_MSG}}", err_html).replace("{{CSRF_TOKEN}}", csrf_token)
    res = HTMLResponse(content=html)
    res.set_cookie(key="barsi_csrf", value=csrf_token, httponly=False, samesite="lax", path="/")
    return res

@app.post("/login")
async def do_login(request: Request):
    global _login_attempts
    client_ip = _get_client_ip(request)
    now = time.time()

    # Previne memory leak lento de IPs antigos
    if len(_login_attempts) > 1000:
        _login_attempts = {
            ip: times for ip, times in _login_attempts.items()
            if times and now - times[-1] < LOGIN_RATE_LIMIT_WINDOW
        }

    recent_attempts = [t for t in _login_attempts.get(client_ip, []) if now - t < LOGIN_RATE_LIMIT_WINDOW]

    if len(recent_attempts) >= LOGIN_RATE_LIMIT_MAX:
        logger.warning("login_rate_limited", ip=client_ip)
        return RedirectResponse(url="/login?error=Muitas+tentativas.+Aguarde+1+minuto.", status_code=303)

    body = (await request.body()).decode("utf-8", errors="ignore")
    params = urllib.parse.parse_qs(body)
    username = params.get("username", [""])[0].strip()
    password = params.get("password", [""])[0].strip()
    totp_code = params.get("totp_code", [""])[0].strip()

    repo = get_user_repo()
    user = repo.authenticate(username, password)
    if user:
        if user.get("totp_enabled"):
            if not totp_code or not repo.verify_totp(user["id"], totp_code):
                recent_attempts.append(now)
                _login_attempts[client_ip] = recent_attempts
                logger.warning("login_totp_failed", ip=client_ip, username=username, attempts=len(recent_attempts))
                return RedirectResponse(url="/login?error=Código+2FA+(TOTP)+inválido+ou+ausente&requires_2fa=1", status_code=303)

        _login_attempts.pop(client_ip, None)
        logger.info("login_success", ip=client_ip, username=username, role=user["role"])
        token = create_session_token(user["id"], user["username"], user["role"], settings.secret_key)
        res = RedirectResponse(url="/", status_code=303)
        res.set_cookie(key="barsi_session", value=token, httponly=True, max_age=86400 * 7, samesite="lax")
        csrf_token = generate_csrf_token(token, settings.secret_key)
        res.set_cookie(key="barsi_csrf", value=csrf_token, httponly=False, max_age=86400 * 7, samesite="lax", path="/")
        return res

    recent_attempts.append(now)
    _login_attempts[client_ip] = recent_attempts
    logger.warning("login_failed", ip=client_ip, username=username, attempts=len(recent_attempts))
    return RedirectResponse(url="/login?error=Usuário+ou+senha+incorretos", status_code=303)

@app.get("/logout")
async def do_logout():
    res = RedirectResponse(url="/login", status_code=303)
    res.delete_cookie(key="barsi_session")
    res.delete_cookie(key="barsi_csrf")
    return res

# ----------------- ROTAS DAS PÁGINAS DEDICADAS -----------------

@app.get("/", response_class=HTMLResponse)
async def page_home(request: Request, error: Optional[str] = None):
    resp = render_page("home", request, "Cockpit Executivo & Radar B.E.S.T.", "home")
    if error and isinstance(resp, Response) and not isinstance(resp, RedirectResponse):
        body_text = resp.body.decode("utf-8")
        toast_injection = (
            f"<script>document.addEventListener('DOMContentLoaded', () => {{"
            f" if (typeof showToast === 'function') showToast({json.dumps(error, ensure_ascii=False)}, 'error');"
            f"}});</script>"
        )
        return HTMLResponse(body_text + toast_injection, status_code=resp.status_code)
    return resp

@app.get("/carteira", response_class=HTMLResponse)
async def page_carteira(request: Request):
    return render_page("carteira", request, "Carteira B.E.S.T. & Custódia Consolidada", "carteira")

@app.get("/aportes", response_class=HTMLResponse)
async def page_aportes(request: Request):
    return render_page("aportes", request, "Simulador de Aportes & Ordens Barsi", "aportes")

@app.get("/budget", response_class=HTMLResponse)
async def page_budget(request: Request):
    return render_page("budget", request, "Orçamento & Fluxo Mensal", "budget")

@app.get("/extratos", response_class=HTMLResponse)
async def page_extratos(request: Request):
    return render_page("extratos", request, "Extratos Bancários (OFX) & Conciliação", "extratos")

@app.get("/insights", response_class=HTMLResponse)
async def page_insights(request: Request):
    return render_page("insights", request, "Central de Insights IA & RLHF", "insights")

@app.get("/analytics", response_class=HTMLResponse)
async def page_analytics(request: Request):
    return render_page("analytics", request, "Análise Patrimonial & Benchmarks", "analytics")

@app.get("/settings", response_class=HTMLResponse)
async def page_settings(request: Request):
    return render_page("settings", request, "Configurações & Conectividade", "settings")

@app.get("/admin", response_class=HTMLResponse)
async def page_admin(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login")
    if user.get("role") != "admin":
        return RedirectResponse(url="/?error=Acesso+restrito+a+administradores", status_code=303)
    return render_page("admin", request, "Administração & Usuários", "admin")

@app.get("/help", response_class=HTMLResponse)
async def page_help(request: Request):
    return render_page("help", request, "Guia Barsi & Metodologia", "help")

# ----------------- APIS REST (BACKEND) -----------------

@app.get("/api/home/summary")
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
            "insight_resumo": p.insight_atual or f"DY Projetado de {p.dy:.1f}%. Margem {p.margem_seguranca:.1f}%.",
            "feedback": p.feedback_usuario
        })

    taxa = round((concordos / validados * 100), 1) if validados > 0 else 100.0
    top_insights.sort(key=lambda x: x["margem"], reverse=True)

    return {
        "patrimonio_total": round(soma_patrimonio, 2),
        "cotas_totais": soma_cotas,
        "aporte_programado": round(soma_aporte, 2),
        "novas_cotas": soma_novas_cotas,
        "ativos_abaixo_teto": abaixo_teto,
        "total_ativos": len(positions),
        "proventos_proj_ano": round(proventos_ano, 2),
        "taxa_concordancia": taxa,
        "top_insights": top_insights[:5]
    }

@app.get("/api/carteira")
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

@app.post("/api/carteira")
async def save_carteira_edits(req: SaveEditsRequest, request: Request):
    require_role_action(request, action_type="edit")
    
    budget_repo = BudgetRepository(settings.sqlite_db_path)
    positions = await get_canonical_positions()
    pos_map = {p.ticker: p for p in positions}

    for item in req.items:
        preco_ref = item.preco_atual or (pos_map[item.ticker].preco_atual if item.ticker in pos_map else 0.0)
        if item.div_proj > 0:
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
            p.div_proj = item.div_proj
            if item.preco_atual is not None and item.preco_atual > 0:
                p.preco_atual = item.preco_atual
            if item.teto_manual is not None:
                p.teto_manual = item.teto_manual
            if item.custo_medio is not None and item.custo_medio >= 0:
                p.custo_medio = item.custo_medio
            if item.preco_teto is not None and item.preco_teto > 0:
                p.preco_teto = item.preco_teto
            if item.qtd_antes is not None:
                p.qtd_antes = max(0, item.qtd_antes)
            if item.qtd_aporte is not None:
                p.qtd_aporte = max(0, item.qtd_aporte)
            p.qtd_total = p.qtd_antes + p.qtd_aporte
            p.valor_total = round(p.qtd_total * p.preco_atual, 2)
            p.total_aporte = round(p.qtd_aporte * p.preco_atual, 2)
            p.feedback_usuario = item.feedback
            atualizar_posicao_quant(p, p.preco_atual, p.div_proj)
        else:
            # Caso a posição não exista previamente, cria com preço informado ou 0.0
            # (Se 0.0, será atualizado na próxima sincronização de mercado via /api/run)
            preco = item.preco_atual if (item.preco_atual is not None and item.preco_atual > 0) else 0.0
            p = Position(
                ticker=item.ticker,
                row_index=item.row_index if item.row_index and item.row_index >= 10 else 10 + len(positions),
                preco_atual=preco,
                div_proj=item.div_proj,
                qtd_antes=max(0, item.qtd_antes or 0),
                qtd_aporte=max(0, item.qtd_aporte or 0),
                preco_teto=item.preco_teto or 0.0,
                teto_manual=bool(item.teto_manual),
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

    return {
        "status": "ok",
        "updated_count": len(req.items)
    }

@app.get("/api/simulador")
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

@app.get("/api/insights")
async def get_insights_api(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autorizado")

    positions = await get_canonical_positions()

    import sqlite3
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

@app.post("/api/insights/feedback/batch")
async def save_insight_feedback_batch(req: FeedbackBatchRequest, request: Request):
    require_role_action(request, action_type="edit")
    if not req.items:
        return {"ok": True, "count": 0}

    budget_repo = BudgetRepository(settings.sqlite_db_path)
    for it in req.items:
        budget_repo.update_insight_feedback(it.ticker, it.feedback)

    return {"ok": True, "count": len(req.items)}

@app.post("/api/insights/feedback")
async def save_insight_feedback(req: FeedbackRequest, request: Request):
    batch_req = FeedbackBatchRequest(items=[FeedbackBatchItem(ticker=req.ticker, feedback=req.feedback)])
    res = await save_insight_feedback_batch(batch_req, request)
    return {
        "ok": res.get("ok", True),
        "ticker": req.ticker,
        "feedback": req.feedback
    }

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

@app.post("/api/snapshot/today")
async def record_today_snapshot(request: Request):
    require_role_action(request, action_type="edit")
    repo = BudgetRepository(settings.sqlite_db_path)
    positions = await get_canonical_positions()
    return _record_snapshot_logic(positions, repo)

_benchmarks_cache: Optional[Dict[str, Any]] = None
_benchmarks_cache_time: float = 0.0

def get_real_benchmarks(snapshots: List[Dict[str, Any]]) -> Dict[str, Any]:
    global _benchmarks_cache, _benchmarks_cache_time
    now = time.time()
    if _benchmarks_cache and (now - _benchmarks_cache_time) < 3600.0:
        return _benchmarks_cache

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
    _benchmarks_cache = result
    _benchmarks_cache_time = now
    return result

@app.get("/api/analytics/benchmarks")
async def get_benchmarks(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autorizado")
    budget_repo = BudgetRepository(settings.sqlite_db_path)
    snapshots = budget_repo.get_portfolio_snapshots(12)
    return await asyncio.to_thread(get_real_benchmarks, snapshots)

@app.get("/api/analytics/data")
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
    snapshots = budget_repo.build_real_portfolio_snapshots(limit_months=months)
    if not snapshots or len(snapshots) < 3:
        budget_repo.seed_initial_snapshots_if_empty(round(soma_patrimonio, 2), soma_cotas, setores)
        snapshots = budget_repo.get_portfolio_snapshots(months)

    # Gera histórico de dividendos dinamicamente a partir dos snapshots
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

# ----------------- ARTEFATOS PERSONALIZADOS (RELATÓRIOS DINÂMICOS) -----------------
@app.get("/api/artifacts/sources")
async def get_artifact_sources(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autorizado")
    repo = BudgetRepository(settings.sqlite_db_path)
    return repo.get_available_data_sources()

@app.get("/api/artifacts")
async def list_artifacts(request: Request, visible_in: Optional[str] = None):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autorizado")
    repo = BudgetRepository(settings.sqlite_db_path)
    items = repo.list_custom_artifacts(visible_in=visible_in)
    return {"items": items}

@app.post("/api/artifacts")
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

@app.delete("/api/artifacts/{artifact_id}")
async def delete_artifact(request: Request, artifact_id: int):
    user = require_role_action(request, action_type="edit")
    repo = BudgetRepository(settings.sqlite_db_path)
    deleted = repo.delete_custom_artifact(artifact_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Artefato não encontrado")
    return {"ok": True, "message": "Artefato excluído com sucesso"}

@app.get("/api/artifacts/{artifact_id}/data")
async def get_artifact_data(request: Request, artifact_id: int):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autorizado")
    repo = BudgetRepository(settings.sqlite_db_path)
    try:
        return repo.get_custom_artifact_data(artifact_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))

@app.get("/api/artifacts/{artifact_id}/export")
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

@app.get("/api/admin/users")
async def list_admin_users(request: Request):
    user = require_role_action(request, action_type="admin")
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Acesso restrito a administradores")
    repo = get_user_repo()
    users = repo.list_users()
    return [{
        "id": u["id"],
        "username": u["username"],
        "role": u["role"],
        "totp_enabled": bool(u.get("totp_enabled")),
        "created_at": u.get("criado_em")
    } for u in users]

# ----------------- ROTAS 2FA (TOTP) RFC 6238 -----------------
@app.post("/api/auth/2fa/setup")
async def setup_2fa(request: Request):
    """Inicia a configuração de 2FA gerando um segredo TOTP e URI para o usuário atual."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autorizado")
    repo = get_user_repo()
    secret_b32, uri = repo.setup_totp(user["uid"])
    return {
        "ok": True,
        "secret": secret_b32,
        "uri": uri,
        "qr_code_url": f"https://api.qrserver.com/v1/create-qr-code/?size=200x200&data={urllib.parse.quote(uri)}"
    }

@app.post("/api/auth/2fa/enable")
async def enable_2fa(req: TotpConfirmRequest, request: Request):
    """Valida o código de 6 dígitos gerado no app e conclui a ativação do 2FA."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autorizado")
    repo = get_user_repo()
    success = repo.confirm_and_enable_totp(user["uid"], req.code)
    if not success:
        raise HTTPException(status_code=400, detail="Código de 6 dígitos inválido ou expirado.")
    return {"ok": True, "message": "2FA (TOTP) ativado com sucesso!"}

@app.post("/api/auth/2fa/disable")
async def disable_2fa(req: TotpConfirmRequest, request: Request):
    """Desativa o 2FA para o usuário atual após validar o código TOTP vigente (proof-of-possession)."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autorizado")
    repo = get_user_repo()
    if not repo.verify_totp(user["uid"], req.code):
        raise HTTPException(status_code=400, detail="Código 2FA (TOTP) inválido ou expirado.")
    repo.disable_totp(user["uid"])
    return {"ok": True, "message": "2FA (TOTP) desativado com sucesso."}

# ----------------- ROTAS DE DIVIDENDOS REAIS (P0) -----------------
@app.get("/api/dividends")
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

@app.post("/api/dividends")
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

# ----------------- ROTAS DE ORÇAMENTO E EXTRATOS (ONDA 2) -----------------
@app.get("/api/budget")
@app.get("/api/budget/{mes_ano}")
async def get_budget_endpoint(request: Request, mes_ano: Optional[str] = None):
    """Retorna comparativo Previsto vs Realizado (Orçamento Barsi) com base em despesas e transações."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autorizado")
    repo = BudgetRepository(settings.sqlite_db_path)
    return repo.get_budget_overview(mes_ano=mes_ano)

@app.get("/api/extratos/transacoes")
async def get_transacoes_endpoint(
    request: Request,
    mes_ano: Optional[str] = None,
    categoria: Optional[str] = None,
    limit: int = 50,
    offset: int = 0
):
    """Lista as transações bancárias/cartão persistidas no SQLite com paginação."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autorizado")
    repo = BudgetRepository(settings.sqlite_db_path)
    rows = repo.list_transacoes(mes_ano=mes_ano, categoria=categoria, limit=limit, offset=offset)
    return {"transacoes": rows, "count": len(rows), "offset": offset}

# ----------------- WEBHOOK INTERATIVO TELEGRAM BOT -----------------
_telegram_run_lock = False

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

@app.post("/api/telegram/webhook")
async def telegram_webhook(request: Request, background_tasks: BackgroundTasks):
    """Webhook do Telegram Bot para comandos interativos (/status, /radar, /proventos, /ticker, /run)."""
    # Valida secret token do Telegram caso configurado
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

    # Whitelist estrita: só aceita mensagens do chat autorizado no .env
    configured_chat = str(settings.telegram_chat_id or "").strip()
    if not configured_chat or chat_id != configured_chat:
        logger.warning("telegram_unauthorized_chat", chat_id=chat_id)
        return {"ok": True}

    cmd = text.split()[0].lower()
    args = text.split()[1:]
    repo = BudgetRepository(settings.sqlite_db_path)
    bot_token = settings.telegram_bot_token

    async def _send_reply(reply_text: str):
        await send_telegram_message(bot_token, chat_id, reply_text)

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
        global _telegram_run_lock
        if _telegram_run_lock:
            await _send_reply("⏳ Já existe uma análise quantitativa em andamento na TV Box. Por favor, aguarde.")
            return {"ok": True}

        _telegram_run_lock = True
        await _send_reply("🚀 Ciclo de análise quantitativa disparado em segundo plano...")
        async def _run_task():
            global _telegram_run_lock
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
                _telegram_run_lock = False

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



@app.post("/api/admin/users")
async def create_admin_user(req: NewUserRequest, request: Request):
    user = require_role_action(request, action_type="admin")
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Acesso restrito a administradores")
    repo = get_user_repo()
    success = repo.create_user(req.username, req.password, req.role)
    if not success:
        raise HTTPException(status_code=400, detail="Nome de usuário já existe ou inválido")
    return {"ok": True, "username": req.username, "role": req.role}

@app.get("/api/backup/download")
async def download_backup_sqlite(request: Request):
    require_role_action(request, action_type="admin")
    db_p = settings.sqlite_db_path
    if not os.path.exists(db_p):
        raise HTTPException(status_code=404, detail="Banco de dados SQLite não encontrado")
    return FileResponse(db_p, filename="barsi_memory.db", media_type="application/octet-stream")

@app.post("/api/dss/reload")
async def reload_dss(request: Request):
    """Recarrega os pesos do modelo DividendSustainabilityScorer em tempo de execução."""
    require_role_action(request, action_type="admin")
    from barsi.domain.quant_ai import DividendSustainabilityScorer
    DividendSustainabilityScorer.reload()
    return {"ok": True, "message": "Pesos DSS recarregados com sucesso"}

@app.post("/api/export/sheets")
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

@app.get("/api/export/xlsx")
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

@app.post("/api/history/sync")
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

@app.post("/api/settings/test/{provider}")
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

@app.post("/api/run")
async def trigger_run_web(request: Request):
    require_role_action(request, action_type="run")
    global LAST_RUN_SUMMARY
    service = get_sync_service()
    summary = await service.execute_run()
    LAST_RUN_SUMMARY = summary

    # Registra snapshot patrimonial consolidado do dia ao finalizar execução
    try:
        repo = BudgetRepository(settings.sqlite_db_path)
        positions = await get_canonical_positions()
        _record_snapshot_logic(positions, repo)
    except Exception as exc:
        logger.warning("run_snapshot_save_failed", error=str(exc))

    return summary.model_dump()

@app.post("/api/extratos")
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

    sheets = AdapterFactory.create_sheets_adapter(settings)
    if hasattr(sheets, "sync_budget_manager"):
        sheets.sync_budget_manager(resumos)
    return {
        "status": "ok",
        "meses": list(resumos.keys()),
        "reconciled_positions": reconciled_count,
        "message": f"Extratos processados. {reconciled_count} ativo(s) conciliado(s) na carteira." if reconciled_count > 0 else "Extratos processados com sucesso."
    }

@app.post("/api/extratos/arquivar")
async def arquivar_extratos_api(request: Request):
    require_role_action(request, action_type="sync")
    movidos = OFXParserService.arquivar_arquivos_processados("extratos", subpasta="processados")
    return {
        "status": "ok",
        "movidos": movidos,
        "message": f"{movidos} arquivo(s) arquivado(s) em 'extratos/processados/'." if movidos > 0 else "Nenhum arquivo pendente para arquivar."
    }

@app.post("/api/upload-extrato")
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

@app.get("/api/health/sync")
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

@app.post("/api/health/sync/reconcile")
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
        return {
            "ok": True,
            "positions_count": len(positions),
            "synced_targets": ["sqlite"]
        }
    else:
        raise HTTPException(status_code=400, detail="Master inválido. Use 'sqlite' ou 'sheets'.")

@app.get("/api/scheduler")
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

@app.post("/api/scheduler")
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

@app.get("/api/notifications/preferences")
async def get_notification_prefs(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autenticado")
    repo = BudgetRepository(settings.sqlite_db_path)
    return {"items": repo.list_notification_preferences()}

@app.post("/api/notifications/preferences")
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
