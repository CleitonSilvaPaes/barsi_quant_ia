"""Pacote de roteadores modulares da API HTTP Barsi Quant IA."""
from barsi.api.routers.pages import router as pages_router
from barsi.api.routers.auth import router as auth_router
from barsi.api.routers.carteira import router as carteira_router
from barsi.api.routers.budget import router as budget_router
from barsi.api.routers.analytics import router as analytics_router
from barsi.api.routers.admin import router as admin_router

__all__ = [
    "pages_router",
    "auth_router",
    "carteira_router",
    "budget_router",
    "analytics_router",
    "admin_router",
]

