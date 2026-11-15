"""Módulo de utilitários, modelos Pydantic, dependências e estado compartilhado da API Barsi Quant IA."""
import os
import time
import hashlib
from typing import List, Optional, Dict, Any, Tuple, Literal
import psutil
from pydantic import BaseModel, Field
from fastapi import HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from barsi.config import settings
from barsi.adapters.factory import AdapterFactory
from barsi.adapters.history.user_repository import UserRepository
from barsi.adapters.history.budget_repository import BudgetRepository
from barsi.infra.security import (
    decode_session_token,
    generate_csrf_token,
)
from barsi.services.sync_service import SyncService
from barsi.domain.models import RunSummary, Position
from barsi.infra.logging import logger

START_TIME = time.time()
LAST_RUN_SUMMARY: Optional[RunSummary] = None

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")

CSRF_EXEMPT_PATHS = {
    "/login",
    "/login/2fa",
    "/auth/google/login",
    "/auth/google/callback",
    "/api/telegram/webhook",
    "/manifest.json",
    "/service-worker.js",
    "/docs",
    "/openapi.json",
    "/redoc",
}

# ----------------- MODELOS PYDANTIC COMPARTILHADOS -----------------

class EditItem(BaseModel):
    ticker: str
    row_index: Optional[int] = 0
    div_proj: Optional[float] = None
    preco_atual: Optional[float] = Field(default=None, ge=0)
    qtd_antes: Optional[int] = Field(default=None, ge=0)
    qtd_aporte: Optional[int] = Field(default=None, ge=0)
    preco_teto: Optional[float] = Field(default=None, ge=0)
    margem: Optional[float] = None
    patrimonio: Optional[float] = Field(default=None, ge=0)
    teto_manual: Optional[bool] = None
    custo_medio: Optional[float] = Field(default=None, ge=0)
    feedback: str = ""

class CategoryRequest(BaseModel):
    name: str
    kind: str = "variavel"
    active: bool = True

class UpdateTransacaoRequest(BaseModel):
    descricao: str
    categoria: str
    valor: float
    data: str
    tipo: Optional[str] = None

class TransacaoFeedbackRequest(BaseModel):
    feedback: Literal["OK_ANOMALIA", "NORMAL", "REVISAR"] = "OK_ANOMALIA"

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
    email: Optional[str] = None

class UpdateUserPasswordRequest(BaseModel):
    new_password: str

class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

class UpdateUserEmailRequest(BaseModel):
    email: str

UpdateEmailRequest = UpdateUserEmailRequest

class ArtifactCreateRequest(BaseModel):
    name: str
    description: Optional[str] = ""
    chart_type: str = "bar"
    data_source: str
    fields: List[str]
    filters: Optional[Dict[str, Any]] = None
    visible_in: Optional[List[str]] = Field(default_factory=lambda: ["analytics"])

class GoalCreateRequest(BaseModel):
    tipo: str
    titulo: str
    valor_alvo: float = Field(..., gt=0)
    ticker: Optional[str] = None
    prazo_meses: Optional[int] = None
    notas: Optional[str] = ""

class GoalUpdateRequest(BaseModel):
    tipo: str
    titulo: str
    valor_alvo: float = Field(..., gt=0)
    ticker: Optional[str] = None
    prazo_meses: Optional[int] = None
    notas: Optional[str] = ""

class DripSimulateRequest(BaseModel):
    valor_disponivel: Optional[float] = None
    mes_ano: Optional[str] = None
    balance_sectors: bool = True
    max_ativos: int = 4

class DripApplyItem(BaseModel):
    ticker: str
    qtd_sugerida: int = Field(..., gt=0)
    valor_gasto: Optional[float] = 0.0

class DripApplyRequest(BaseModel):
    ordens: List[DripApplyItem]

class SnowballSimulateRequest(BaseModel):
    aporte_mensal: float = Field(default=1000.0, ge=0)
    taxa_reinvestimento: float = Field(default=1.0, ge=0, le=1.0)
    anos: int = Field(default=20, ge=1, le=50)
    dy_base: Optional[float] = Field(default=None, ge=0)
    crescimento_anual_proventos: float = Field(default=6.0, ge=0)
    valor_inicial: Optional[float] = Field(default=None, ge=0)

# ----------------- ESTADO GLOBAL E CACHES -----------------

_user_repo_cache: Optional[UserRepository] = None
_canonical_positions_cache: Dict[int, Tuple[List[Position], float]] = {}
_benchmarks_cache: Optional[Dict[str, Any]] = None
_benchmarks_cache_time: float = 0.0
_last_telemetry_cache: Dict[str, Any] = {}
_last_telemetry_time: float = 0.0
_login_attempts: Dict[str, List[float]] = {}
LOGIN_RATE_LIMIT_MAX = 5
LOGIN_RATE_LIMIT_WINDOW = 60.0

def _cleanup_login_attempts(now: float):
    global _login_attempts
    if len(_login_attempts) > 1000:
        _login_attempts = {
            ip: times for ip, times in _login_attempts.items()
            if times and now - times[-1] < LOGIN_RATE_LIMIT_WINDOW
        }

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

def is_request_secure(request: Optional[Request]) -> bool:
    """Verifica se a requisição trafega sobre HTTPS direto ou através de proxy reverso/Tailscale."""
    if not request:
        return False
    proto = request.headers.get("x-forwarded-proto", "").lower()
    return proto == "https" or request.url.scheme == "https"

def set_barsi_cookie(
    response: Response,
    key: str,
    value: str,
    request: Optional[Request] = None,
    httponly: bool = True,
    max_age: Optional[int] = 86400 * 7,
    samesite: str = "lax",
    path: str = "/"
):
    """Define cookie com atributos de segurança (HttpOnly, SameSite e Secure em HTTPS)."""
    secure_flag = is_request_secure(request)
    response.set_cookie(
        key=key,
        value=value,
        httponly=httponly,
        max_age=max_age,
        samesite=samesite,
        path=path,
        secure=secure_flag
    )

def get_user_repo() -> UserRepository:
    try:
        import barsi.api.http as http_mod
        fn = http_mod.__dict__.get("get_user_repo")
        if fn and fn is not get_user_repo:
            return fn()
    except Exception:
        pass

    global _user_repo_cache
    # Sincroniza com monkeypatch em testes (ex: conftest.py)
    try:
        import barsi.api.http as http_mod
        if hasattr(http_mod, "_user_repo_cache") and http_mod._user_repo_cache is None and _user_repo_cache is not None:
            _user_repo_cache = None
    except Exception:
        pass

    target_path = os.path.abspath(settings.sqlite_db_path)
    if _user_repo_cache is None or os.path.abspath(_user_repo_cache.db_path) != target_path:
        _user_repo_cache = UserRepository(target_path)
        try:
            import barsi.api.http as http_mod
            http_mod._user_repo_cache = _user_repo_cache
        except Exception:
            pass

        if settings.initial_admin_user and settings.initial_admin_pass:
            _user_repo_cache.init_admin_if_empty(settings.initial_admin_user, settings.initial_admin_pass)
        elif not _user_repo_cache.list_users():
            logger.warning(
                "no_admin_user_configured",
                hint="Defina BARSI_INITIAL_ADMIN_USER e BARSI_INITIAL_ADMIN_PASS no .env ou rode `barsi user-add`"
            )
    return _user_repo_cache

def get_sync_service() -> SyncService:
    try:
        import barsi.api.http as http_mod
        fn = http_mod.__dict__.get("get_sync_service")
        if fn and fn is not get_sync_service:
            return fn()
    except Exception:
        pass

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

def get_user_budget_repo(request: Optional[Request] = None, user_id: Optional[int] = None) -> BudgetRepository:
    """Instancia o BudgetRepository isolado para o usuário da requisição atual (Multi-tenancy)."""
    try:
        import barsi.api.http as http_mod
        fn = http_mod.__dict__.get("get_user_budget_repo")
        if fn and fn is not get_user_budget_repo:
            return fn(request=request, user_id=user_id)
    except Exception:
        pass

    if user_id is not None:
        uid = int(user_id)
    elif request is not None:
        user = get_current_user(request)
        uid = int(user.get("uid", 1)) if user else 1
    else:
        uid = 1
    return BudgetRepository(settings.sqlite_db_path, user_id=uid)

def invalidate_canonical_positions_cache(user_id: Optional[int] = None):
    """Invalida o cache de posições canônicas após alterações no banco."""
    global _canonical_positions_cache
    if user_id is not None:
        _canonical_positions_cache.pop(user_id, None)
    else:
        _canonical_positions_cache.clear()

async def get_canonical_positions(user_id: int = 1, use_cache: bool = True) -> List[Position]:
    """Obtém as posições canônicas do SQLite (fonte da verdade) para o usuário especificado com cache LRU curto."""
    try:
        import barsi.api.http as http_mod
        import asyncio
        fn = http_mod.__dict__.get("get_canonical_positions")
        if fn and fn is not get_canonical_positions:
            res = fn(user_id=user_id, use_cache=use_cache)
            if asyncio.iscoroutine(res):
                return await res
            return res
    except Exception:
        pass

    now = time.time()
    if use_cache and not os.getenv("PYTEST_CURRENT_TEST") and user_id in _canonical_positions_cache:
        cached, ts = _canonical_positions_cache[user_id]
        if now - ts < 5.0:
            return [Position(**p.model_dump()) for p in cached]

    budget_repo = BudgetRepository(settings.sqlite_db_path, user_id=user_id)
    positions = budget_repo.load_positions()
    if not positions and (os.getenv("PYTEST_CURRENT_TEST") or settings.env in ("test", "testing")):
        try:
            sheets = AdapterFactory.create_sheets_adapter(settings)
            positions = await sheets.load_positions()
            if positions:
                budget_repo.save_positions(positions)
                logger.info("sqlite_test_seeded_from_sheets", count=len(positions))
        except Exception as e:
            logger.warning("sheets_test_seed_failed", error=str(e))

    if positions:
        _canonical_positions_cache[user_id] = (positions, now)
    return positions

def get_current_user(request: Request) -> Optional[Dict[str, Any]]:
    """Extrai e valida o token de sessão criptografado via HMAC-SHA256, verificando revogação de JTI."""
    try:
        import barsi.api.http as http_mod
        fn = http_mod.__dict__.get("get_current_user")
        if fn and fn is not get_current_user:
            return fn(request)
    except Exception:
        pass

    token = request.cookies.get("barsi_session")
    if not token:
        return None
    data = decode_session_token(token, settings.secret_key)
    if not data:
        return None
    jti = data.get("jti")
    repo = get_user_repo()
    if jti and repo.is_session_revoked(jti):
        return None
    sv = data.get("sv")
    if sv is not None and "uid" in data:
        user_db = repo.get_by_id(data["uid"])
        if not user_db:
            logger.warning("token_for_deleted_user", uid=data["uid"], jti=jti)
            return None
        if user_db.get("session_version", 1) != sv:
            return None
    return data

def require_role_action(request: Request, action_type: str = "edit") -> Dict[str, Any]:
    """RBAC centralizado. Valida permissões pelo papel do usuário sem regras dispersas."""
    try:
        import barsi.api.http as http_mod
        fn = http_mod.__dict__.get("require_role_action")
        if fn and fn is not require_role_action:
            return fn(request, action_type=action_type)
    except Exception:
        pass

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

def get_cached_system_telemetry(ttl: float = 10.0) -> Dict[str, Any]:
    """Retorna métricas do sistema com cache TTL para evitar syscalls de psutil a cada renderização."""
    global _last_telemetry_cache, _last_telemetry_time
    now = time.time()
    if _last_telemetry_cache and (now - _last_telemetry_time < ttl):
        return _last_telemetry_cache
    mem = psutil.virtual_memory()
    proc = psutil.Process()
    uptime_sec = int(now - START_TIME)
    uptime_str = f"{uptime_sec // 3600}h {(uptime_sec % 3600) // 60}m"
    free_mb = round(mem.available / (1024 * 1024), 1)
    total_mb = round(mem.total / (1024 * 1024), 1)
    rss_mb = round(proc.memory_info().rss / (1024 * 1024), 1)
    _last_telemetry_cache = {
        "uptime_str": uptime_str,
        "free_mb": free_mb,
        "total_mb": total_mb,
        "rss_mb": rss_mb
    }
    _last_telemetry_time = now
    return _last_telemetry_cache


def get_or_create_session_csrf(
    session_token: Optional[str],
    secret_key: str,
    user_repo: Optional[UserRepository] = None
) -> str:
    """Retorna o token CSRF vinculado à sessão na tabela session_csrf, gerando e persistindo se necessário."""
    if not session_token:
        return generate_csrf_token(None, secret_key)
    if user_repo is None:
        user_repo = get_user_repo()

    session_data = decode_session_token(session_token, secret_key)
    session_id = session_data.get("jti") if session_data else hashlib.sha256(session_token.encode("utf-8")).hexdigest()

    existing = user_repo.get_session_csrf(session_id)
    if existing:
        return existing

    uid = session_data.get("uid") if session_data else None
    return user_repo.rotate_session_csrf(session_id, user_id=uid)


def render_page(template_name: str, request: Request, page_title: str, active_nav: str) -> Response:
    """Renderiza páginas compartilhando o layout mestre base.html com sidebar e telemetria."""
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login")

    telem = get_cached_system_telemetry(ttl=10.0)
    uptime_str = telem["uptime_str"]
    free_mb = telem["free_mb"]
    total_mb = telem["total_mb"]
    rss_mb = telem["rss_mb"]

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
    navs = ["home", "carteira", "metas", "aportes", "budget", "extratos", "insights", "analytics", "settings", "admin", "help"]

    is_admin = (user.get("role") == "admin")
    admin_nav_html = f'<a href="/admin" class="nav-item {"active" if active_nav == "admin" else ""}"><span class="icon">🔐</span> Admin & Usuários</a>' if is_admin else ""

    session_token = request.cookies.get("barsi_session", "")
    csrf_token = get_or_create_session_csrf(session_token, settings.secret_key)

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
    set_barsi_cookie(response=res, key="barsi_csrf", value=csrf_token, request=request, httponly=False, samesite="lax", path="/")
    return res

