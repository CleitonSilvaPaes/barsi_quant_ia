"""Adapter de persistência local SQLite com histórico e tabela de runs."""
import sqlite3
import os
import asyncio
from typing import Optional, Tuple
from barsi.ports.history import HistoryPort
from barsi.domain.models import Position, InsightResult, RunSummary
from barsi.infra.logging import logger
from barsi.adapters.history.budget_repository import INIT_BUDGET_SCHEMA_SQL

class SQLiteHistoryAdapter(HistoryPort):
    def __init__(self, db_path: Optional[str] = None):
        from barsi.adapters.history.budget_repository import resolve_db_path

        self.db_path = resolve_db_path(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_tables()

    def _init_tables(self):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS historico_analises (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT,
                    ticker TEXT,
                    data_analise TEXT,
                    preco REAL,
                    preco_teto REAL,
                    margem_seguranca REAL,
                    resumo_web TEXT,
                    insight_ia TEXT,
                    acao_sugerida TEXT,
                    feedback_usuario TEXT,
                    provider TEXT,
                    model TEXT,
                    latency_ms INTEGER,
                    fallback_used INTEGER,
                    UNIQUE(run_id, ticker)
                )
            """)
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    timestamp TEXT,
                    duration_ms INTEGER,
                    tickers_analyzed INTEGER,
                    memory_peak_mb REAL,
                    errors TEXT
                )
            """)
            cursor.executescript(INIT_BUDGET_SCHEMA_SQL)
            conn.commit()

    async def get_last_analysis(self, ticker: str) -> Optional[Tuple[str, str, str]]:
        def _get():
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT data_analise, insight_ia, feedback_usuario 
                    FROM historico_analises 
                    WHERE ticker = ? 
                    ORDER BY id DESC LIMIT 1
                """, (ticker,))
                row = cursor.fetchone()
                return (row[0], row[1], row[2]) if row else None

        return await asyncio.to_thread(_get)

    async def save_analysis(
        self,
        run_id: str,
        posicao: Position,
        insight: InsightResult,
        noticias: str
    ) -> None:
        def _save():
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT OR REPLACE INTO historico_analises 
                    (run_id, ticker, data_analise, preco, preco_teto, margem_seguranca, resumo_web, 
                     insight_ia, acao_sugerida, feedback_usuario, provider, model, latency_ms, fallback_used)
                    VALUES (?, ?, datetime('now'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    run_id,
                    posicao.ticker,
                    posicao.preco_atual,
                    posicao.preco_teto,
                    posicao.margem_seguranca,
                    noticias,
                    insight.insight,
                    insight.acao_sugerida.value,
                    posicao.feedback_usuario,
                    insight.provider,
                    insight.model,
                    insight.latency_ms,
                    1 if insight.fallback_used else 0
                ))
                conn.commit()

        await asyncio.to_thread(_save)

    async def record_run(self, summary: RunSummary) -> None:
        def _record():
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT OR REPLACE INTO runs 
                    (run_id, timestamp, duration_ms, tickers_analyzed, memory_peak_mb, errors)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    summary.run_id,
                    summary.timestamp.isoformat(),
                    summary.duration_ms,
                    summary.tickers_analyzed,
                    summary.memory_peak_mb,
                    "; ".join(summary.errors)
                ))
                conn.commit()

        await asyncio.to_thread(_record)

