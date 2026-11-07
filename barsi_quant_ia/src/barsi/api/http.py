"""API HTTP Minimalista (FastAPI) para Healthcheck e Gatilho Remoto (Cloudflare)."""
import time
import psutil
from fastapi import FastAPI, Depends, HTTPException, Security
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from barsi.config import settings
from barsi.adapters.factory import AdapterFactory
from barsi.services.sync_service import SyncService
from barsi.domain.models import RunSummary
from barsi.infra.logging import logger

app = FastAPI(title="Barsi Quant IA - Edge Service", version="1.0.0")
security = HTTPBearer()

START_TIME = time.time()
LAST_RUN_SUMMARY: RunSummary = None

@app.get("/", response_class=HTMLResponse)
async def dashboard():
    mem = psutil.virtual_memory()
    proc = psutil.Process()
    uptime_sec = int(time.time() - START_TIME)
    uptime_str = f"{uptime_sec // 3600}h {(uptime_sec % 3600) // 60}m {uptime_sec % 60}s"
    free_mb = round(mem.available / (1024 * 1024), 1)
    total_mb = round(mem.total / (1024 * 1024), 1)
    rss_mb = round(proc.memory_info().rss / (1024 * 1024), 1)

    html = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Barsi Quant IA — Telemetria Edge</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; margin: 0; padding: 2rem; }}
    .container {{ max-width: 800px; margin: 0 auto; }}
    .header {{ display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid #334155; padding-bottom: 1rem; margin-bottom: 2rem; }}
    .title {{ font-size: 1.5rem; font-weight: bold; color: #38bdf8; display: flex; align-items: center; gap: 0.5rem; }}
    .badge {{ background: #10b981; color: #042f2e; padding: 0.25rem 0.75rem; border-radius: 9999px; font-weight: 600; font-size: 0.85rem; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 1rem; margin-bottom: 2rem; }}
    .card {{ background: #1e293b; border: 1px solid #334155; border-radius: 0.75rem; padding: 1.25rem; }}
    .card h3 {{ margin: 0 0 0.5rem 0; font-size: 0.875rem; color: #94a3b8; text-transform: uppercase; letter-spacing: 0.05em; }}
    .card p {{ margin: 0; font-size: 1.5rem; font-weight: 700; color: #f1f5f9; }}
    .links {{ display: flex; gap: 1rem; flex-wrap: wrap; }}
    a.btn {{ display: inline-block; background: #2563eb; color: #fff; text-decoration: none; padding: 0.75rem 1.25rem; border-radius: 0.5rem; font-weight: 600; transition: background 0.2s; }}
    a.btn:hover {{ background: #1d4ed8; }}
    a.btn-secondary {{ background: #334155; color: #e2e8f0; }}
    a.btn-secondary:hover {{ background: #475569; }}
  </style>
</head>
<body>
  <div class="container">
    <div class="header">
      <div class="title">📺 Barsi Quant IA — Telemetria</div>
      <div class="badge">● ONLINE</div>
    </div>
    
    <div class="grid">
      <div class="card">
        <h3>Memória RAM Livre</h3>
        <p>{free_mb} MB <span style="font-size: 0.875rem; font-weight: normal; color: #94a3b8;">de {total_mb} MB</span></p>
      </div>
      <div class="card">
        <h3>Uso do Processo</h3>
        <p>{rss_mb} MB RSS</p>
      </div>
      <div class="card">
        <h3>Tempo Online</h3>
        <p>{uptime_str}</p>
      </div>
      <div class="card">
        <h3>Motor de Decisão</h3>
        <p style="font-size: 1.1rem; color: #38bdf8;">{settings.ai_provider.upper()}</p>
      </div>
    </div>

    <div class="card" style="margin-bottom: 2rem;">
      <h3>Endpoints Disponíveis</h3>
      <p style="font-size: 1rem; color: #cbd5e1; margin-bottom: 1rem;">
        Consulte as métricas e endpoints REST para integração ou monitoramento externo:
      </p>
      <div class="links">
        <a href="/health" class="btn">JSON Healthcheck (/health)</a>
        <a href="/metrics" class="btn btn-secondary">Métricas Prometheus (/metrics)</a>
      </div>
    </div>
  </div>
</body>
</html>"""
    return HTMLResponse(content=html)

def get_sync_service() -> SyncService:
    market = AdapterFactory.create_market_adapter(settings)
    news = AdapterFactory.create_news_adapter(settings)
    ai = AdapterFactory.create_ai_adapter(settings)
    sheets = AdapterFactory.create_sheets_adapter(settings)
    history = AdapterFactory.create_history_adapter(settings)
    return SyncService(
        market=market,
        news=news,
        ai=ai,
        sheets=sheets,
        history=history,
        max_concurrency=settings.max_concurrency
    )

def verify_token(credentials: HTTPAuthorizationCredentials = Security(security)):
    if credentials.credentials != settings.api_bearer_token:
        raise HTTPException(status_code=403, detail="Acesso não autorizado")
    return credentials.credentials

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

@app.post("/run", dependencies=[Depends(verify_token)])
async def trigger_run(dry_run: bool = False, ticker: str = None):
    global LAST_RUN_SUMMARY
    service = get_sync_service()
    summary = await service.execute_run(dry_run=dry_run, target_ticker=ticker)
    LAST_RUN_SUMMARY = summary
    return summary

@app.get("/metrics")
async def metrics():
    mem = psutil.virtual_memory()
    proc = psutil.Process()
    lines = [
        "# HELP barsi_uptime_seconds Tempo de atividade em segundos",
        "# TYPE barsi_uptime_seconds gauge",
        f"barsi_uptime_seconds {int(time.time() - START_TIME)}",
        "# HELP barsi_process_rss_bytes Memoria residente do processo",
        "# TYPE barsi_process_rss_bytes gauge",
        f"barsi_process_rss_bytes {proc.memory_info().rss}",
        "# HELP barsi_system_available_bytes Memoria livre no sistema operacional",
        "# TYPE barsi_system_available_bytes gauge",
        f"barsi_system_available_bytes {mem.available}"
    ]
    return "\n".join(lines) + "\n"

