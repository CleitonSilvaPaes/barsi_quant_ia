"""API HTTP Minimalista (FastAPI) para Healthcheck e Gatilho Remoto (Cloudflare)."""
import time
import psutil
from fastapi import FastAPI, Depends, HTTPException, Security
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

