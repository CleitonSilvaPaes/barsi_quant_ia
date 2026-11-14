"""API HTTP & Painel Web Interativo do Ecossistema Barsi Quant IA.

Ponto de entrada FastAPI, configuração de middlewares de segurança (CSP, CSRF, GZip),
arquivos estáticos e inclusão dos roteadores modulares (routers/*.py).
"""
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware

from barsi.config import settings
from barsi.infra.logging import logger
from barsi.infra.security import generate_csrf_token, verify_csrf_token

from barsi.domain.models import Position
from barsi.adapters.factory import AdapterFactory
from barsi.adapters.history.user_repository import UserRepository
from barsi.adapters.history.budget_repository import BudgetRepository
from barsi.services.sync_service import SyncService
from barsi.services.ofx_service import OFXParserService
from barsi.services.b3_custodia_service import B3CustodiaService
from barsi.services.drip_service import DripService
from barsi.services.monthly_closing_service import MonthlyClosingService
from barsi.services.early_warning_service import EarlyWarningService
from barsi.services.snowball_backtest_service import SnowballBacktestService

from barsi.api.common import (
    START_TIME,
    LAST_RUN_SUMMARY,
    STATIC_DIR,
    TEMPLATES_DIR,
    CSRF_EXEMPT_PATHS,
    _user_repo_cache,
    _canonical_positions_cache,
    _benchmarks_cache,
    _benchmarks_cache_time,
    _last_telemetry_cache,
    _last_telemetry_time,
    _login_attempts,
    LOGIN_RATE_LIMIT_MAX,
    LOGIN_RATE_LIMIT_WINDOW,
    EditItem,
    CategoryRequest,
    UpdateTransacaoRequest,
    TransacaoFeedbackRequest,
    DividendItem,
    TotpConfirmRequest,
    SchedulerUpdate,
    NotifPrefUpdate,
    SaveEditsRequest,
    FeedbackRequest,
    FeedbackBatchItem,
    FeedbackBatchRequest,
    NewUserRequest,
    UpdateUserPasswordRequest,
    ChangePasswordRequest,
    UpdateUserEmailRequest,
    UpdateEmailRequest,
    ArtifactCreateRequest,
    GoalCreateRequest,
    GoalUpdateRequest,
    DripSimulateRequest,
    DripApplyItem,
    DripApplyRequest,
    SnowballSimulateRequest,
    is_request_secure,
    set_barsi_cookie,
    _cleanup_login_attempts,
    _get_client_ip,
    get_user_repo,
    get_sync_service,
    get_user_budget_repo,
    invalidate_canonical_positions_cache,
    get_canonical_positions,
    get_current_user,
    require_role_action,
    get_cached_system_telemetry,
    render_page,
)
from barsi.api.routers import (
    pages_router,
    auth_router,
    carteira_router,
    budget_router,
    analytics_router,
    admin_router,
)
from barsi.api.routers.analytics import send_telegram_message

_telegram_run_lock = False

__all__ = [
    "app",
    "lifespan",
    "security",
    "_telegram_run_lock",
    "START_TIME",
    "LAST_RUN_SUMMARY",
    "STATIC_DIR",
    "TEMPLATES_DIR",
    "CSRF_EXEMPT_PATHS",
    "_user_repo_cache",
    "_canonical_positions_cache",
    "_benchmarks_cache",
    "_benchmarks_cache_time",
    "_last_telemetry_cache",
    "_last_telemetry_time",
    "_login_attempts",
    "LOGIN_RATE_LIMIT_MAX",
    "LOGIN_RATE_LIMIT_WINDOW",
    "EditItem",
    "CategoryRequest",
    "UpdateTransacaoRequest",
    "TransacaoFeedbackRequest",
    "DividendItem",
    "TotpConfirmRequest",
    "SchedulerUpdate",
    "NotifPrefUpdate",
    "SaveEditsRequest",
    "FeedbackRequest",
    "FeedbackBatchItem",
    "FeedbackBatchRequest",
    "NewUserRequest",
    "UpdateUserPasswordRequest",
    "ChangePasswordRequest",
    "UpdateUserEmailRequest",
    "UpdateEmailRequest",
    "ArtifactCreateRequest",
    "GoalCreateRequest",
    "GoalUpdateRequest",
    "DripSimulateRequest",
    "DripApplyItem",
    "DripApplyRequest",
    "SnowballSimulateRequest",
    "is_request_secure",
    "set_barsi_cookie",
    "_cleanup_login_attempts",
    "_get_client_ip",
    "get_user_repo",
    "get_sync_service",
    "get_user_budget_repo",
    "invalidate_canonical_positions_cache",
    "get_canonical_positions",
    "get_current_user",
    "require_role_action",
    "get_cached_system_telemetry",
    "render_page",
    "send_telegram_message",
    "Position",
    "AdapterFactory",
    "UserRepository",
    "BudgetRepository",
    "SyncService",
    "OFXParserService",
    "B3CustodiaService",
    "DripService",
    "MonthlyClosingService",
    "EarlyWarningService",
    "SnowballBacktestService",
]

@asynccontextmanager
async def lifespan(app: FastAPI):
    if settings.telegram_bot_token and not (settings.telegram_webhook_secret or "").strip():
        logger.warning(
            "telegram_webhook_secret_missing",
            hint="Configure BARSI_TELEGRAM_WEBHOOK_SECRET e secret_token no setWebhook para proteção extra contra spoofing."
        )
    yield

app = FastAPI(title="Barsi Quant IA - Web Suite", version="2.0.0", lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=1000)
security = HTTPBearer(auto_error=False)

os.makedirs(STATIC_DIR, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

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
        set_barsi_cookie(
            response=response,
            key="barsi_csrf",
            value=token_csrf,
            request=request,
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
    response.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=(), payment=()"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline'; "
        "font-src 'self' data:; "
        "img-src 'self' data: https:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self';"
    )
    if is_request_secure(request):
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    if request.url.path.startswith("/static"):
        response.headers["Cache-Control"] = "public, max-age=86400, stale-while-revalidate=604800"
    return response

# Inclusão dos roteadores modulares
app.include_router(pages_router)
app.include_router(auth_router)
app.include_router(carteira_router)
app.include_router(budget_router)
app.include_router(analytics_router)
app.include_router(admin_router)
