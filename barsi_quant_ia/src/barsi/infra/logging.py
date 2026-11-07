"""Configuração de Logging Estruturado em JSON (structlog)."""
import structlog
import logging
import sys
import os

def setup_logging(log_level: str = "INFO", log_file: str = "memory/barsi.log"):
    os.makedirs(os.path.dirname(log_file), exist_ok=True)
    
    level = getattr(logging, log_level.upper(), logging.INFO)
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=level
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer()
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True
    )

logger = structlog.get_logger("barsi")

