"""Pacote de roteadores modulares da API HTTP Barsi Quant IA."""
from barsi.api.routers.pages import router as pages_router
from barsi.api.routers.auth import router as auth_router
from barsi.api.routers.carteira import router as carteira_router
from barsi.api.routers.budget import router as budget_router
from barsi.api.routers.analytics import router as analytics_router
from barsi.api.routers.admin import router as admin_router
from barsi.api.routers.scheduler import router as scheduler_router
from barsi.api.routers.artifacts import router as artifacts_router
from barsi.api.routers.insights import router as insights_router
from barsi.api.routers.telegram import router as telegram_router, send_telegram_message
from barsi.api.routers.wrapped import router as wrapped_router

__all__ = [
    "pages_router",
    "auth_router",
    "carteira_router",
    "budget_router",
    "analytics_router",
    "admin_router",
    "scheduler_router",
    "artifacts_router",
    "insights_router",
    "telegram_router",
    "wrapped_router",
    "send_telegram_message",
]

