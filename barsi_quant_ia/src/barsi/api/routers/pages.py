"""Rotas de visualização HTML e manifesto/service-worker PWA."""
import os
import json
from typing import Optional
from fastapi import APIRouter, Request, Response, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse

from barsi.api.common import (
    STATIC_DIR,
    render_page,
    get_current_user,
)

router = APIRouter(tags=["pages"])

# ----------------- ROTAS PWA (PROGRESSIVE WEB APP) -----------------
@router.get("/manifest.json")
async def get_manifest():
    """Entrega o manifesto do Progressive Web App (PWA)."""
    manifest_path = os.path.join(STATIC_DIR, "manifest.json")
    if not os.path.exists(manifest_path):
        raise HTTPException(status_code=404, detail="Manifest não encontrado")
    return FileResponse(manifest_path, media_type="application/manifest+json")

@router.get("/service-worker.js")
async def get_service_worker():
    """Entrega o Service Worker do PWA com escopo raiz."""
    sw_path = os.path.join(STATIC_DIR, "service-worker.js")
    if not os.path.exists(sw_path):
        raise HTTPException(status_code=404, detail="Service worker não encontrado")
    return FileResponse(
        sw_path,
        media_type="application/javascript",
        headers={"Service-Worker-Allowed": "/"}
    )

# ----------------- ROTAS DAS PÁGINAS DEDICADAS -----------------
@router.get("/", response_class=HTMLResponse)
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

@router.get("/carteira", response_class=HTMLResponse)
async def page_carteira(request: Request):
    return render_page("carteira", request, "Carteira B.E.S.T. & Custódia Consolidada", "carteira")

@router.get("/metas", response_class=HTMLResponse)
async def page_metas(request: Request):
    return render_page("metas", request, "Dashboard de Metas & Objetivos Financeiros", "metas")

@router.get("/aportes", response_class=HTMLResponse)
async def page_aportes(request: Request):
    return render_page("aportes", request, "Simulador de Aportes & Ordens Barsi", "aportes")

@router.get("/budget", response_class=HTMLResponse)
async def page_budget(request: Request):
    return render_page("budget", request, "Orçamento & Fluxo Mensal", "budget")

@router.get("/extratos", response_class=HTMLResponse)
async def page_extratos(request: Request):
    return render_page("extratos", request, "Extratos Bancários (OFX) & Conciliação", "extratos")

@router.get("/insights", response_class=HTMLResponse)
async def page_insights(request: Request):
    return render_page("insights", request, "Central de Insights IA & RLHF", "insights")

@router.get("/analytics", response_class=HTMLResponse)
async def page_analytics(request: Request):
    return render_page("analytics", request, "Análise Patrimonial & Benchmarks", "analytics")

@router.get("/wrapped", response_class=HTMLResponse)
async def page_wrapped(request: Request):
    return render_page("wrapped", request, "Barsi Wrapped — Retrospectiva Anual", "wrapped")

@router.get("/settings", response_class=HTMLResponse)
async def page_settings(request: Request):
    return render_page("settings", request, "Configurações & Conectividade", "settings")

@router.get("/admin", response_class=HTMLResponse)
async def page_admin(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login")
    if user.get("role") != "admin":
        return RedirectResponse(url="/?error=Acesso+restrito+a+administradores", status_code=303)
    return render_page("admin", request, "Administração & Usuários", "admin")

@router.get("/help", response_class=HTMLResponse)
async def page_help(request: Request):
    return render_page("help", request, "Guia Barsi & Metodologia", "help")

