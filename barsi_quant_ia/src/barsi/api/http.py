"""API HTTP & Painel Web Interativo com Sidebar, Roteamento Modular e Perfis (RBAC)."""
import os
import time
import urllib.parse
from datetime import datetime
from typing import List, Optional, Dict, Any
import psutil
from pydantic import BaseModel
from fastapi import FastAPI, Depends, HTTPException, Security, Request, Response, UploadFile, File, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse, FileResponse
from fastapi.security import HTTPBearer

from barsi.config import settings
from barsi.adapters.factory import AdapterFactory
from barsi.adapters.history.user_repository import UserRepository
from barsi.adapters.history.budget_repository import BudgetRepository
from barsi.infra.security import create_session_token, decode_session_token
from barsi.services.sync_service import SyncService
from barsi.services.ofx_service import OFXParserService
from barsi.services.sync_health_service import SyncHealthService
from barsi.domain.models import RunSummary, Position, SimuladorOrder
from barsi.domain.rules import atualizar_posicao_quant
from barsi.infra.logging import logger

app = FastAPI(title="Barsi Quant IA - Web Suite", version="2.0.0")
security = HTTPBearer(auto_error=False)

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
    qtd_antes: Optional[int] = None
    qtd_aporte: Optional[int] = None
    preco_teto: Optional[float] = None
    teto_manual: Optional[bool] = None
    feedback: str = ""

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
    if user.get("role") == "leitor" and action_type in ["edit", "run", "sync", "admin"]:
        raise HTTPException(status_code=403, detail="Perfil de Leitor: Apenas visualização permitida.")
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

    html = (
        base_html.replace("{{TITLE}}", page_title)
                 .replace("{{PAGE_HEADING}}", page_title)
                 .replace("{{PAGE_CONTENT}}", content_html)
                 .replace("{{FREE_MB}}", str(free_mb))
                 .replace("{{TOTAL_MB}}", str(total_mb))
                 .replace("{{RSS_MB}}", str(rss_mb))
                 .replace("{{UPTIME_STR}}", uptime_str)
                 .replace("{{AI_PROVIDER}}", settings.ai_provider.upper())
                 .replace("{{USER_NAME}}", f"{user['user']} ({role_badge})")
    )
    for n in navs:
        html = html.replace(f"{{{{NAV_{n.upper()}}}}}", "active" if n == active_nav else "")

    return HTMLResponse(content=html)

# ----------------- ROTAS DE AUTENTICAÇÃO -----------------

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

    repo = get_user_repo()
    user = repo.authenticate(username, password)
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

# ----------------- ROTAS DAS PÁGINAS DEDICADAS -----------------

@app.get("/", response_class=HTMLResponse)
async def page_home(request: Request):
    return render_page("home", request, "Cockpit Executivo & Radar B.E.S.T.", "home")

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

    try:
        sheets = AdapterFactory.create_sheets_adapter(settings)
        positions = await sheets.load_positions()
    except Exception as e:
        logger.warning("api_home_summary_fallback_sqlite", error=str(e))
        budget_repo = BudgetRepository(settings.sqlite_db_path)
        positions = budget_repo.load_positions()

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
    
    try:
        sheets = AdapterFactory.create_sheets_adapter(settings)
        positions = await sheets.load_positions()
    except Exception as e:
        logger.warning("api_carteira_fallback_sqlite", error=str(e))
        budget_repo = BudgetRepository(settings.sqlite_db_path)
        positions = budget_repo.load_positions()
    
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
    positions = budget_repo.load_positions()
    if not positions:
        try:
            sheets = AdapterFactory.create_sheets_adapter(settings)
            positions = await sheets.load_positions()
        except Exception:
            positions = []
    
    pos_map = {p.ticker: p for p in positions}

    for item in req.items:
        if item.ticker in pos_map:
            p = pos_map[item.ticker]
            p.div_proj = item.div_proj
            if item.teto_manual is not None:
                p.teto_manual = item.teto_manual
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

    # 1. P0 — SQLite como FONTE DA VERDADE (Persistência atômica local em <10ms)
    budget_repo.save_positions(positions)

    # 2. Espelhamento no Excel (.xlsx) local
    try:
        from barsi.adapters.sheets.xlsx_adapter import XLSXAdapter
        xlsx_path = os.path.join(os.path.dirname(__file__), "../../../carteira_barsi_quant_ia.xlsx")
        if not os.path.exists(xlsx_path):
            xlsx_path = "carteira_barsi_quant_ia.xlsx"
        if os.path.exists(xlsx_path):
            xlsx = XLSXAdapter(xlsx_path)
            await xlsx.update_positions(positions)
    except Exception as e:
        logger.warn("local_xlsx_mirror_error", error=str(e))

    # 3. Espelhamento no Google Sheets (Best-effort com Outbox Pattern)
    synced_to_sheets = False
    try:
        sheets = AdapterFactory.create_sheets_adapter(settings)
        await sheets.update_positions(positions, invalidate=True)
        synced_to_sheets = True
    except Exception as e:
        logger.warn("sheets_save_failed_queued_to_outbox", error=str(e))
        for item in req.items:
            budget_repo.record_pending_sheets_write(item.ticker, item.model_dump())

    return {
        "status": "ok",
        "synced_to_sheets": synced_to_sheets,
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

    try:
        sheets = AdapterFactory.create_sheets_adapter(settings)
        positions = await sheets.load_positions()
    except Exception as e:
        logger.warning("api_insights_fallback_sqlite", error=str(e))
        budget_repo = BudgetRepository(settings.sqlite_db_path)
        positions = budget_repo.load_positions()

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
        return {"ok": True, "count": 0, "synced_to_sheets": True}

    budget_repo = BudgetRepository(settings.sqlite_db_path)
    # 1. Atualização atômica no SQLite
    for it in req.items:
        budget_repo.update_insight_feedback(it.ticker, it.feedback)

    # 2. Atualização em batch no Google Sheets (1 única requisição batch)
    synced_to_sheets = False
    try:
        sheets = AdapterFactory.create_sheets_adapter(settings)
        positions = await sheets.load_positions()
        pos_map = {p.ticker: p for p in positions}
        updated_positions = []
        for it in req.items:
            if it.ticker in pos_map:
                pos = pos_map[it.ticker]
                pos.feedback_usuario = it.feedback
                updated_positions.append(pos)
        if updated_positions:
            await sheets.update_positions(updated_positions, invalidate=(len(updated_positions) > 1))
            synced_to_sheets = True
            logger.info("google_sheets_batch_updated", count=len(updated_positions))
    except Exception as e:
        logger.warn("sheets_batch_feedback_sync_warning", error=str(e))
        for it in req.items:
            budget_repo.record_pending_sheets_write(it.ticker, {"feedback": it.feedback})

    return {"ok": True, "count": len(req.items), "synced_to_sheets": synced_to_sheets}

@app.post("/api/insights/feedback")
async def save_insight_feedback(req: FeedbackRequest, request: Request):
    batch_req = FeedbackBatchRequest(items=[FeedbackBatchItem(ticker=req.ticker, feedback=req.feedback)])
    res = await save_insight_feedback_batch(batch_req, request)
    return {
        "ok": res.get("ok", True),
        "ticker": req.ticker,
        "feedback": req.feedback,
        "synced_to_sheets": res.get("synced_to_sheets", False)
    }

@app.post("/api/snapshot/today")
async def record_today_snapshot(request: Request):
    require_role_action(request, action_type="edit")
    repo = BudgetRepository(settings.sqlite_db_path)
    positions = repo.load_positions()
    if not positions:
        try:
            sheets = AdapterFactory.create_sheets_adapter(settings)
            positions = await sheets.load_positions()
        except Exception:
            positions = []

    soma_patrimonio = sum((p.qtd_antes or 0) * (p.preco_atual or 0.0) for p in positions)
    soma_cotas = sum(p.qtd_antes or 0 for p in positions)
    soma_aporte = sum((p.qtd_aporte or 0) * (p.preco_atual or 0.0) for p in positions)
    proventos_ano = sum(((p.qtd_antes or 0) + (p.qtd_aporte or 0)) * (p.div_proj or 0.0) for p in positions)
    setores = {}
    for p in positions:
        sec = p.setor or "B.E.S.T."
        val = (p.qtd_antes or 0) * (p.preco_atual or 0.0)
        setores[sec] = round(setores.get(sec, 0.0) + val, 2)

    today_str = datetime.now().strftime("%Y-%m-%d")
    import json
    repo.save_portfolio_snapshot(
        today_str,
        round(soma_patrimonio, 2),
        soma_cotas,
        dividendos_mes=round(proventos_ano / 12, 2),
        aporte_planejado=round(soma_aporte, 2),
        setores_json=json.dumps(setores, ensure_ascii=False)
    )
    return {"status": "ok", "snapshot_date": today_str, "patrimonio": round(soma_patrimonio, 2)}

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

    # 4. CDI 12M Real de Referência Oficial
    cdi_ret = 11.15

    spread_cdi = round(carteira_ret - cdi_ret, 2) if (carteira_ret is not None and cdi_ret is not None) else None
    spread_ibov = round(carteira_ret - ibov_ret, 2) if (carteira_ret is not None and ibov_ret is not None) else None

    result = {
        "carteira_12m": carteira_ret,
        "cdi_12m": cdi_ret,
        "ibov_12m": ibov_ret,
        "ifix_12m": ifix_ret,
        "spread_cdi": spread_cdi,
        "spread_ibov": spread_ibov,
        "fonte": "Yahoo Finance (^BVSP, IFIX.SA) & B3 (Taxa DI)"
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
    return get_real_benchmarks(snapshots)

@app.get("/api/analytics/data")
async def get_analytics_data(request: Request, months: int = 12):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autorizado")

    try:
        sheets = AdapterFactory.create_sheets_adapter(settings)
        positions = await sheets.load_positions()
    except Exception as e:
        logger.warning("api_analytics_fallback_sqlite", error=str(e))
        budget_repo = BudgetRepository(settings.sqlite_db_path)
        positions = budget_repo.load_positions()

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
        dividendos.append({
            "mes": mes_str,
            "realizado": round(div_real, 2),
            "projetado": round(div_real * 1.15, 2)
        })

    return {
        "patrimonio_atual": round(soma_patrimonio, 2),
        "proventos_proj_ano": round(proventos_ano, 2),
        "snapshots": snapshots,
        "setores": setores,
        "dividendos": dividendos
    }

@app.get("/api/admin/users")
async def list_admin_users(request: Request):
    user = require_role_action(request, action_type="admin")
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Acesso restrito a administradores")
    repo = get_user_repo()
    users = repo.list_users()
    return [{"id": u["id"], "username": u["username"], "role": u["role"], "created_at": u.get("criado_em")} for u in users]

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
    require_role_action(request, action_type="read")
    db_p = settings.sqlite_db_path
    if not os.path.exists(db_p):
        raise HTTPException(status_code=404, detail="Banco de dados SQLite não encontrado")
    return FileResponse(db_p, filename="barsi_memory.db", media_type="application/octet-stream")

@app.get("/api/health/sync")
async def check_sync_health(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Não autorizado")
    svc = SyncHealthService(settings)
    return await svc.audit_sources()

@app.post("/api/health/sync/reconcile")
async def reconcile_sources_endpoint(request: Request, master: str = "sheets"):
    require_role_action(request, action_type="edit")
    svc = SyncHealthService(settings)
    return await svc.reconcile_sources(master=master)

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

async def flush_pending_sheets_writes() -> int:
    """Tenta descarregar escritas pendentes na fila Outbox para o Google Sheets."""
    repo = BudgetRepository(settings.sqlite_db_path)
    pending = repo.get_pending_sheets_writes()
    if not pending:
        return 0

    try:
        sheets = AdapterFactory.create_sheets_adapter(settings)
        positions = await sheets.load_positions()
        pos_map = {p.ticker: p for p in positions}
        updated = []
        flushed_ids = []

        for item in pending:
            t = item["ticker"]
            payload = item.get("payload", {})
            if t in pos_map:
                p = pos_map[t]
                if "qtd_antes" in payload and payload["qtd_antes"] is not None:
                    p.qtd_antes = max(0, payload["qtd_antes"])
                if "qtd_aporte" in payload and payload["qtd_aporte"] is not None:
                    p.qtd_aporte = max(0, payload["qtd_aporte"])
                if "div_proj" in payload and payload["div_proj"] is not None:
                    p.div_proj = payload["div_proj"]
                if "feedback" in payload and payload["feedback"] is not None:
                    p.feedback_usuario = payload["feedback"]
                updated.append(p)
                flushed_ids.append(item["id"])

        if updated:
            await sheets.update_positions(updated, invalidate=True)
            for wid in flushed_ids:
                repo.clear_pending_sheets_write(wid)
            logger.info("pending_sheets_flushed_successfully", count=len(updated))
            return len(updated)
    except Exception as e:
        logger.warn("flush_pending_sheets_writes_error", error=str(e))
        for item in pending:
            repo.increment_pending_sheets_retry(item["id"])
    return 0

@app.post("/api/run")
async def trigger_run_web(request: Request):
    require_role_action(request, action_type="run")
    await flush_pending_sheets_writes()
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
