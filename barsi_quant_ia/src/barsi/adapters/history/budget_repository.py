"""Repositório de orçamento, despesas recorrentes e transações OFX persistidas.

Esta é a ÚNICA fonte da verdade para valores monetários mensais.
Regra permanente: NENHUM valor monetário mensal pode ser hardcoded no código.
Se o valor não existe em `recurring_expenses`, ele NÃO EXISTE.
"""
from __future__ import annotations

import os
import sqlite3
import json
import calendar
from datetime import datetime, timedelta
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple

from barsi.domain.models import Position
from barsi.infra.logging import logger

INIT_BUDGET_SCHEMA_SQL = """
    -- 1. Categorias canônicas de despesa (fonte única da verdade)
    CREATE TABLE IF NOT EXISTS expense_categories (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        name        TEXT NOT NULL UNIQUE,
        kind        TEXT NOT NULL CHECK (kind IN ('fixa','variavel','tributo','reserva','investimento','entrada')),
        active      INTEGER NOT NULL DEFAULT 1,
        created_at  TEXT NOT NULL DEFAULT (datetime('now'))
    );

    -- 2. Despesas recorrentes com janela de validade (substitui if m == "...")
    CREATE TABLE IF NOT EXISTS recurring_expenses (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        category_id  INTEGER NOT NULL REFERENCES expense_categories(id),
        amount       REAL NOT NULL CHECK (amount >= 0),
        valid_from   TEXT NOT NULL,            -- 'YYYY-MM-01'
        valid_to     TEXT,                     -- NULL = vigente
        source       TEXT NOT NULL DEFAULT 'seed',  -- 'seed'|'manual'|'ofx'
        notes        TEXT,
        created_at   TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_recurring_lookup
        ON recurring_expenses(category_id, valid_from, valid_to);
    CREATE UNIQUE INDEX IF NOT EXISTS idx_recurring_unique_period
        ON recurring_expenses(category_id, valid_from);

    -- 3. Transações OFX persistidas (o extrato vira tabela, deixa de ser volátil)
    CREATE TABLE IF NOT EXISTS transacoes (
        id                INTEGER PRIMARY KEY AUTOINCREMENT,
        fitid             TEXT NOT NULL,
        data              TEXT NOT NULL,        -- 'YYYY-MM-DD'
        mes_ano           TEXT NOT NULL,        -- 'YYYY-MM' (derivado, indexado)
        tipo              TEXT NOT NULL,        -- 'CREDIT'|'DEBIT'
        valor             REAL NOT NULL,
        descricao         TEXT NOT NULL,
        categoria         TEXT NOT NULL DEFAULT 'Outros',
        ticker            TEXT,
        origem            TEXT NOT NULL DEFAULT 'CONTA',   -- 'CONTA'|'CARTAO'
        categoria_id      INTEGER REFERENCES expense_categories(id),
        conciliado        INTEGER NOT NULL DEFAULT 0,
        UNIQUE(fitid, tipo, valor)
    );
    CREATE INDEX IF NOT EXISTS idx_transacoes_mes ON transacoes(mes_ano, categoria);

    -- 4. Snapshot mensal de budget (auditoria + consulta rápida)
    CREATE TABLE IF NOT EXISTS budget_entries (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        mes_ano      TEXT NOT NULL,             -- 'YYYY-MM'
        category_id  INTEGER NOT NULL REFERENCES expense_categories(id),
        amount       REAL NOT NULL,
        source       TEXT NOT NULL,             -- 'ofx'|'recurring'|'manual'|'default'
        updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(mes_ano, category_id)
    );

    -- 5. Posições persistidas (o pipeline passa a poder reconstruir carteira do zero)
    CREATE TABLE IF NOT EXISTS positions (
        ticker              TEXT PRIMARY KEY,
        empresa             TEXT,
        setor               TEXT,
        qtd_antes           INTEGER DEFAULT 0,
        qtd_aporte          INTEGER DEFAULT 0,
        preco_atual         REAL DEFAULT 0,
        div_proj            REAL DEFAULT 0,
        preco_teto          REAL DEFAULT 0,
        margem_seguranca    REAL DEFAULT 0,
        feedback_usuario    TEXT DEFAULT '',
        insight_atual       TEXT DEFAULT '',
        teto_manual         INTEGER DEFAULT 0,
        custo_medio         REAL DEFAULT 0.0,
        updated_at          TEXT DEFAULT (datetime('now'))
    );

    -- 6. Tabela de palavras-chave para categorização sem hardcode no Python
    CREATE TABLE IF NOT EXISTS category_keywords (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        keyword      TEXT NOT NULL,
        category     TEXT NOT NULL,
        bucket       TEXT NOT NULL,  -- 'variavel' | 'tributo' | 'entrada' | 'investimento' | 'fixa' | 'reserva'
        priority     INTEGER DEFAULT 100,
        created_at   TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(keyword, category)
    );

    -- 7. Histórico de notificações para desduplicação diária (máx 1 alerta por ticker/dia)
    CREATE TABLE IF NOT EXISTS notification_log (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker       TEXT NOT NULL,
        alert_type   TEXT NOT NULL,
        sent_date    TEXT NOT NULL DEFAULT (date('now')),
        sent_at      TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(ticker, alert_type, sent_date)
    );

    -- 8. Snapshots mensais de patrimônio para a curva histórica (Analytics & Home)
    CREATE TABLE IF NOT EXISTS portfolio_snapshots (
        id                   INTEGER PRIMARY KEY AUTOINCREMENT,
        data_snapshot        TEXT NOT NULL, -- 'YYYY-MM-DD'
        patrimonio_total     REAL NOT NULL DEFAULT 0,
        cotas_totais         INTEGER NOT NULL DEFAULT 0,
        dividendos_mes       REAL NOT NULL DEFAULT 0,
        aporte_planejado     REAL NOT NULL DEFAULT 0,
        rentabilidade_mes    REAL DEFAULT 0,
        setores_json         TEXT DEFAULT '{}',
        created_at           TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(data_snapshot)
    );

    -- 9. Histórico de Análises da IA para RLHF e explicabilidade
    CREATE TABLE IF NOT EXISTS historico_analises (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id TEXT,
        ticker TEXT,
        data_analise TEXT DEFAULT (datetime('now')),
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
    );

    -- 10. Histórico Diário de Preços Reais para Gráficos e Curva de Patrimônio
    CREATE TABLE IF NOT EXISTS price_history (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker           TEXT NOT NULL,
        data_pregao      TEXT NOT NULL, -- 'YYYY-MM-DD'
        preco_fechamento REAL NOT NULL,
        volume           REAL DEFAULT 0,
        created_at       TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(ticker, data_pregao)
    );
    CREATE INDEX IF NOT EXISTS idx_price_history_lookup ON price_history(ticker, data_pregao);

    -- 11. Fila de Escrita Pendente no Google Sheets (Outbox Pattern)
    CREATE TABLE IF NOT EXISTS pending_sheets_writes (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker       TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        created_at   TEXT NOT NULL DEFAULT (datetime('now')),
        retry_count  INTEGER DEFAULT 0
    );

    -- 12. Cache Persistente das Posições do Google Sheets em SQLite
    CREATE TABLE IF NOT EXISTS sheets_positions_cache (
        ticker       TEXT PRIMARY KEY,
        payload_json TEXT NOT NULL,
        expires_at   TEXT NOT NULL,
        updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
    );

    -- 13. Dividendos Reais Recebidos (P0: dividends_received)
    CREATE TABLE IF NOT EXISTS dividends_received (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker           TEXT NOT NULL,
        data_pagamento   TEXT NOT NULL, -- 'YYYY-MM-DD'
        mes_ano          TEXT NOT NULL, -- 'YYYY-MM'
        valor_total      REAL NOT NULL CHECK (valor_total >= 0),
        valor_por_cota   REAL NOT NULL DEFAULT 0.0,
        quantidade       INTEGER NOT NULL DEFAULT 0,
        tipo             TEXT NOT NULL DEFAULT 'DIVIDENDO', -- 'DIVIDENDO' | 'JCP' | 'RENDIMENTO'
        fonte            TEXT NOT NULL DEFAULT 'manual',    -- 'manual' | 'ofx' | 'b3'
        created_at       TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_dividends_ticker ON dividends_received(ticker, data_pagamento);
    CREATE INDEX IF NOT EXISTS idx_dividends_mes ON dividends_received(mes_ano);

    -- 14. Histórico de Execuções (Runs do Pipeline)
    CREATE TABLE IF NOT EXISTS runs (
        run_id           TEXT PRIMARY KEY,
        timestamp        TEXT DEFAULT (datetime('now')),
        duration_ms      INTEGER,
        tickers_analyzed INTEGER,
        memory_peak_mb   REAL,
        errors           TEXT
    );

    -- 15. Configuração do Scheduler (Singleton id = 1)
    CREATE TABLE IF NOT EXISTS scheduler_config (
        id            INTEGER PRIMARY KEY CHECK (id = 1),  -- singleton
        enabled       INTEGER NOT NULL DEFAULT 1,
        days_of_week  TEXT NOT NULL DEFAULT '1,2,3,4,5',   -- ISO: 1=Seg..7=Dom
        hours         TEXT NOT NULL DEFAULT '9,10,11,12,13,14,15,16,17,18',
        minute        INTEGER NOT NULL DEFAULT 0 CHECK (minute BETWEEN 0 AND 59),
        updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
        updated_by    TEXT
    );

    -- 16. Preferências de Notificação Proativa
    CREATE TABLE IF NOT EXISTS notification_preferences (
        alert_type    TEXT PRIMARY KEY,
        enabled       INTEGER NOT NULL DEFAULT 1,
        updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
        updated_by    TEXT
    );
"""

DEFAULT_KEYWORDS: List[Tuple[str, str, str, int]] = [
    # Despesas Variáveis
    ("SUPERMERCADO", "Despesas Variáveis", "variavel", 100),
    ("ARCHER", "Despesas Variáveis", "variavel", 100),
    ("KOCH", "Despesas Variáveis", "variavel", 100),
    ("POSTO", "Despesas Variáveis", "variavel", 100),
    ("BARBEARIA", "Despesas Variáveis", "variavel", 100),
    ("RESTAURANTE", "Despesas Variáveis", "variavel", 100),
    ("DELIVERY", "Despesas Variáveis", "variavel", 100),
    ("FARMACIA", "Despesas Variáveis", "variavel", 100),
    ("DROGARIA", "Despesas Variáveis", "variavel", 100),
    ("FARMAJU", "Despesas Variáveis", "variavel", 100),
    ("LANCHONETE", "Despesas Variáveis", "variavel", 100),
    ("LANCHES", "Despesas Variáveis", "variavel", 100),
    ("BURGUER", "Despesas Variáveis", "variavel", 100),
    ("SABOR", "Despesas Variáveis", "variavel", 100),
    ("CANTINA", "Despesas Variáveis", "variavel", 100),
    ("VEST", "Despesas Variáveis", "variavel", 100),
    ("MODAS", "Despesas Variáveis", "variavel", 100),
    ("RECARGA", "Despesas Variáveis", "variavel", 100),
    # Tributos
    ("DAS-SIMPLES", "Tributos & Taxas", "tributo", 100),
    ("SEFAZ", "Tributos & Taxas", "tributo", 100),
    ("DETRAN", "Tributos & Taxas", "tributo", 100),
    ("LICENCIAMENTO", "Tributos & Taxas", "tributo", 100),
    ("TRIBUTO", "Tributos & Taxas", "tributo", 100),
    # Cooperativa
    ("AILOS", "Cooperativa Ailos", "fixa", 100),
    ("VIACREDI", "Cooperativa Ailos", "fixa", 90),
    # Reserva
    ("RDB", "Reserva / RDB", "reserva", 100),
    ("CAIXINHA", "Reserva / RDB", "reserva", 100),
    ("RESERVA", "Reserva / RDB", "reserva", 100),
]

DEFAULT_BUDGET_SEEDS: List[Tuple[str, float, str, str]] = [
    ("Saldo Inicial Nov/26", 50.28, "2026-11-01", "reserva"),
    ("Aluguel", 521.30, "2026-10-01", "fixa"),
    ("Compromisso Familiar", 400.00, "2026-10-01", "fixa"),
    ("DAS Simples Nacional", 86.05, "2026-10-01", "tributo"),
    ("Licenciamento Detran", 149.37, "2026-11-01", "tributo"),
    ("Cooperativa Ailos", 60.00, "2026-10-01", "fixa"),
    ("Saldo Ant. Caixa", 180.44, "2026-11-01", "reserva"),
    ("Gastos Variáveis A", 420.00, "2026-10-01", "variavel"),
    ("Gastos Variáveis B", 50.00, "2026-10-01", "variavel"),
    ("Gastos Variáveis C", 110.00, "2026-10-01", "variavel"),
    ("Gastos Variáveis D", 60.00, "2026-10-01", "variavel"),
    ("Gastos Variáveis E", 50.00, "2026-10-01", "variavel"),
    ("Gastos Variáveis A", 450.00, "2026-11-01", "variavel"),
    ("Gastos Variáveis B", 120.00, "2026-11-01", "variavel"),
    ("Gastos Variáveis C", 120.00, "2026-11-01", "variavel"),
    ("Gastos Variáveis D", 65.00, "2026-11-01", "variavel"),
]


@dataclass(frozen=True)
class RecurringExpense:
    category: str
    amount: float
    valid_from: str
    valid_to: Optional[str]
    source: str


def resolve_db_path(path: str = "./memory/barsi_memory.db") -> str:
    """Resolve o caminho do SQLite com segurança para execução na raiz ou em subpastas."""
    if os.path.isabs(path):
        return path
    if os.path.exists(path):
        return os.path.abspath(path)

    # Procura em subdiretório barsi_quant_ia
    sub_path = os.path.join("barsi_quant_ia", path.lstrip("./"))
    if os.path.exists(sub_path):
        return os.path.abspath(sub_path)

    # Procura no diretório pai
    parent_path = os.path.join("..", path.lstrip("./"))
    if os.path.exists(parent_path):
        return os.path.abspath(parent_path)

    return os.path.abspath(path)


# Fatores da curva histórica sintética para seed inicial de visualização (6 meses precedentes).
# Simulam a evolução progressiva do portfólio no primeiro acesso antes da consolidação de snapshots reais.
DEFAULT_SYNTHETIC_CURVE_FACTORS = (0.45, 0.55, 0.67, 0.78, 0.90, 1.0)

DEFAULT_SCHEDULER_DAYS  = "1,2,3,4,5"
DEFAULT_SCHEDULER_HOURS = "9,10,11,12,13,14,15,16,17,18"

DEFAULT_NOTIFICATION_PREFS: List[Tuple[str, int]] = [
    ("margem_20",      1),   # alertas individuais >= 20% de margem
    ("radar_summary",  1),   # resumo horário consolidado
]

VALID_ALERT_TYPES = {alert_type for alert_type, _ in DEFAULT_NOTIFICATION_PREFS}

class BudgetRepository:
    """Leitura e escrita de orçamento no SQLite. Sem I/O de rede, sem dependência do Google Sheets."""

    def __init__(self, db_path: str = "./memory/barsi_memory.db"):
        self.db_path = resolve_db_path(db_path)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_tables()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_tables(self) -> None:
        with self._conn() as conn:
            conn.executescript(INIT_BUDGET_SCHEMA_SQL)
            try:
                cols = [r["name"] for r in conn.execute("PRAGMA table_info(portfolio_snapshots)").fetchall()]
                if "setores_json" not in cols:
                    conn.execute("ALTER TABLE portfolio_snapshots ADD COLUMN setores_json TEXT DEFAULT '{}'")
                cols_pos = [r["name"] for r in conn.execute("PRAGMA table_info(positions)").fetchall()]
                if "teto_manual" not in cols_pos:
                    conn.execute("ALTER TABLE positions ADD COLUMN teto_manual INTEGER DEFAULT 0")
                if "custo_medio" not in cols_pos:
                    conn.execute("ALTER TABLE positions ADD COLUMN custo_medio REAL DEFAULT 0.0")
            except Exception:
                pass
        self.seed_default_keywords()
        self.seed_default_notification_prefs()

    def seed_default_keywords(self) -> int:
        """Popula a tabela category_keywords de forma estritamente idempotente."""
        inserted = 0
        with self._conn() as c:
            for kw, cat, bucket, prio in DEFAULT_KEYWORDS:
                cur = c.execute("""
                    INSERT OR IGNORE INTO category_keywords (keyword, category, bucket, priority)
                    VALUES (?, ?, ?, ?)
                """, (kw, cat, bucket, prio))
                inserted += cur.rowcount
        return inserted

    def get_keywords(self) -> List[Tuple[str, str, str, int]]:
        """Retorna keywords ordenadas por prioridade decrescente e comprimento decrescente."""
        with self._conn() as c:
            rows = c.execute("""
                SELECT keyword, category, bucket, priority
                  FROM category_keywords
                 ORDER BY priority DESC, LENGTH(keyword) DESC
            """).fetchall()
            return [(r["keyword"], r["category"], r["bucket"], r["priority"]) for r in rows]

    def keywords_version(self) -> int:
        """Retorna uma chave de versão para invalidação de cache."""
        with self._conn() as c:
            row = c.execute("SELECT count(*), coalesce(max(id), 0) FROM category_keywords").fetchone()
            return int((row[0] or 0) + (row[1] or 0))

    def upsert_keyword(self, keyword: str, category: str, bucket: str = "variavel", priority: int = 100) -> None:
        """Insere ou atualiza uma palavra-chave para classificação automática de transações."""
        kw = keyword.strip().upper()
        with self._conn() as conn:
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_cat_keywords_kw ON category_keywords(keyword);")
            conn.execute(
                """
                INSERT INTO category_keywords (keyword, category, bucket, priority)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(keyword) DO UPDATE SET
                    category = excluded.category,
                    bucket = excluded.bucket,
                    priority = excluded.priority;
                """,
                (kw, category, bucket, priority),
            )
            conn.commit()

    def delete_keyword(self, keyword: str) -> bool:
        """Remove uma palavra-chave do banco."""
        kw = keyword.strip().upper()
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM category_keywords WHERE keyword = ?", (kw,))
            conn.commit()
            return cur.rowcount > 0

    def seed_defaults(self) -> int:
        """Popula as despesas recorrentes padrão de forma 100% idempotente."""
        for cat, amt, dt, kind in DEFAULT_BUDGET_SEEDS:
            self.upsert_recurring(cat, amt, dt, kind=kind, source="seed")
        return len(DEFAULT_BUDGET_SEEDS)

    # ------------------------------------------------------------------ #
    # Leitura — substitui magic numbers hardcoded
    # ------------------------------------------------------------------ #
    def get_expense_amount(self, mes_ano: str, category: str) -> float:
        """Resolve o valor mensal de uma categoria.

        Prioridade:
        1. Snapshot conciliado (budget_entries) tem prioridade (dado real > estimado).
        2. Despesas recorrentes vigentes (recurring_expenses).
        """
        with self._conn() as c:
            # 1. Snapshot conciliado
            row = c.execute("""
                SELECT be.amount
                  FROM budget_entries be
                  JOIN expense_categories ec ON ec.id = be.category_id
                 WHERE ec.name = ? AND be.mes_ano = ?
                 LIMIT 1
            """, (category, mes_ano)).fetchone()
            if row:
                return float(row["amount"])

            # 2. Recorrente vigente
            mes_iso = f"{mes_ano}-01"
            row = c.execute("""
                SELECT re.amount
                  FROM recurring_expenses re
                  JOIN expense_categories ec ON ec.id = re.category_id
                 WHERE ec.name = ?
                   AND re.valid_from <= ?
                   AND (re.valid_to IS NULL OR re.valid_to >= ?)
                 ORDER BY re.valid_from DESC
                 LIMIT 1
            """, (category, mes_iso, mes_iso)).fetchone()
            if row:
                return float(row["amount"])

            return 0.0

    def get_all_expenses(self, mes_ano: str) -> Dict[str, float]:
        """Retorna {categoria: valor} para um mês em 1 query consolidada."""
        mes_iso = f"{mes_ano}-01"
        res: Dict[str, float] = {}
        with self._conn() as c:
            # 1. Despesas recorrentes vigentes
            rows_recurring = c.execute("""
                WITH latest AS (
                    SELECT re.category_id, re.amount,
                           ROW_NUMBER() OVER (PARTITION BY re.category_id
                                              ORDER BY re.valid_from DESC) AS rn
                      FROM recurring_expenses re
                     WHERE re.valid_from <= ?
                       AND (re.valid_to IS NULL OR re.valid_to >= ?)
                )
                SELECT ec.name, latest.amount
                  FROM latest
                  JOIN expense_categories ec ON ec.id = latest.category_id
                 WHERE latest.rn = 1
            """, (mes_iso, mes_iso)).fetchall()
            for r in rows_recurring:
                res[r["name"]] = float(r["amount"])

            # 2. Snapshot conciliado do mês (budget_entries) sobrepõe (dado real > estimado)
            rows_snapshot = c.execute("""
                SELECT ec.name, be.amount
                  FROM budget_entries be
                  JOIN expense_categories ec ON ec.id = be.category_id
                 WHERE be.mes_ano = ?
            """, (mes_ano,)).fetchall()
            for r in rows_snapshot:
                res[r["name"]] = float(r["amount"])

        return res

    # ------------------------------------------------------------------ #
    # Escrita — usada pelo migration script e pelo CLI `barsi budget set`
    # ------------------------------------------------------------------ #
    def upsert_recurring(
        self,
        category: str,
        amount: float,
        valid_from: str,
        valid_to: Optional[str] = None,
        kind: str = "fixa",
        source: str = "manual",
        notes: Optional[str] = None,
    ) -> None:
        """Cria/atualiza uma despesa recorrente de forma estritamente idempotente.

        Se já existir vigente no mesmo período, fecha a anterior em `valid_from - 1 dia`
        e faz upsert com ON CONFLICT(category_id, valid_from).
        """
        with self._conn() as c:
            c.execute("""INSERT OR IGNORE INTO expense_categories (name, kind)
                         VALUES (?, ?)""", (category, kind))
            row = c.execute("SELECT id FROM expense_categories WHERE name = ?", (category,)).fetchone()
            cat_id = row["id"]

            # Fecha vigência anterior aberta (apenas se for anterior à data informada)
            c.execute("""
                UPDATE recurring_expenses
                   SET valid_to = date(?, '-1 day')
                 WHERE category_id = ? AND valid_to IS NULL AND valid_from < ?
            """, (valid_from, cat_id, valid_from))

            c.execute("""
                INSERT INTO recurring_expenses
                    (category_id, amount, valid_from, valid_to, source, notes)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(category_id, valid_from) DO UPDATE SET
                    amount = excluded.amount,
                    valid_to = excluded.valid_to,
                    source = excluded.source,
                    notes = excluded.notes
            """, (cat_id, amount, valid_from, valid_to, source, notes))
        logger.info("recurring_expense_upserted", category=category, amount=amount, valid_from=valid_from)

    def list_recurring(self) -> List[Dict[str, Any]]:
        """Lista todas as despesas recorrentes cadastradas."""
        with self._conn() as c:
            rows = c.execute("""
                SELECT re.id, ec.name as category, ec.kind, re.amount,
                       re.valid_from, re.valid_to, re.source, re.notes
                  FROM recurring_expenses re
                  JOIN expense_categories ec ON ec.id = re.category_id
                 ORDER BY ec.kind, ec.name, re.valid_from DESC
            """).fetchall()
            return [dict(r) for r in rows]

    # ------------------------------------------------------------------ #
    # OFX → transações (persistência do extrato para não ser volátil)
    # ------------------------------------------------------------------ #
    def persist_transacoes(self, transacoes: Iterable[Any]) -> int:
        """Grava transações no SQLite com deduplicação rigorosa por (fitid, tipo, valor)."""
        inserted = 0
        with self._conn() as c:
            for t in transacoes:
                fitid = getattr(t, "id_transacao", None) or (t.get("id_transacao") if isinstance(t, dict) else "")
                data = getattr(t, "data", None) or (t.get("data") if isinstance(t, dict) else "")
                tipo = getattr(t, "tipo", None) or (t.get("tipo") if isinstance(t, dict) else "")
                valor = getattr(t, "valor", 0.0) if hasattr(t, "valor") else (t.get("valor", 0.0) if isinstance(t, dict) else 0.0)
                descricao = getattr(t, "descricao", None) or (t.get("descricao") if isinstance(t, dict) else "")
                categoria = getattr(t, "categoria", None) or (t.get("categoria", "Outros") if isinstance(t, dict) else "Outros")
                ticker = getattr(t, "ticker_relacionado", None) or (t.get("ticker_relacionado") if isinstance(t, dict) else None)
                origem = getattr(t, "origem", None) or (t.get("origem", "CONTA") if isinstance(t, dict) else "CONTA")

                cat_row = c.execute("SELECT id FROM expense_categories WHERE name = ?", (categoria,)).fetchone()
                cat_id = cat_row["id"] if cat_row else None

                cur = c.execute("""
                    INSERT OR IGNORE INTO transacoes
                        (fitid, data, mes_ano, tipo, valor, descricao,
                         categoria, ticker, origem, categoria_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    fitid, data, data[:7] if data else "", tipo, valor, descricao,
                    categoria, ticker, origem, cat_id
                ))
                inserted += cur.rowcount
        logger.info("transacoes_persisted", inserted=inserted)
        return inserted

    def record_budget_entry(self, mes_ano: str, category: str, amount: float, source: str = "ofx") -> None:
        """Registra snapshot mensal de orçamento."""
        with self._conn() as c:
            c.execute("""INSERT OR IGNORE INTO expense_categories (name, kind) VALUES (?, 'variavel')""", (category,))
            cat_row = c.execute("SELECT id FROM expense_categories WHERE name = ?", (category,)).fetchone()
            cat_id = cat_row["id"]

            c.execute("""
                INSERT INTO budget_entries (mes_ano, category_id, amount, source, updated_at)
                VALUES (?, ?, ?, ?, datetime('now'))
                ON CONFLICT(mes_ano, category_id) DO UPDATE SET
                    amount = excluded.amount,
                    source = excluded.source,
                    updated_at = datetime('now')
            """, (mes_ano, cat_id, amount, source))

    # ------------------------------------------------------------------ #
    # Posições da Carteira (desacoplamento total do Google Sheets)
    # ------------------------------------------------------------------ #
    def persist_positions(self, positions: List[Position]) -> int:
        """Persiste ou atualiza posições da carteira no SQLite."""
        count = 0
        with self._conn() as c:
            for p in positions:
                cur = c.execute("""
                    INSERT INTO positions (
                        ticker, empresa, setor, qtd_antes, qtd_aporte,
                        preco_atual, div_proj, preco_teto, margem_seguranca,
                        feedback_usuario, insight_atual, teto_manual, custo_medio, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                    ON CONFLICT(ticker) DO UPDATE SET
                        empresa = excluded.empresa,
                        setor = excluded.setor,
                        qtd_antes = excluded.qtd_antes,
                        qtd_aporte = excluded.qtd_aporte,
                        preco_atual = excluded.preco_atual,
                        div_proj = excluded.div_proj,
                        preco_teto = excluded.preco_teto,
                        margem_seguranca = excluded.margem_seguranca,
                        feedback_usuario = excluded.feedback_usuario,
                        insight_atual = excluded.insight_atual,
                        teto_manual = excluded.teto_manual,
                        custo_medio = excluded.custo_medio,
                        updated_at = datetime('now')
                """, (
                    p.ticker, p.empresa, p.setor, p.qtd_antes, p.qtd_aporte,
                    p.preco_atual, p.div_proj, p.preco_teto, p.margem_seguranca,
                    p.feedback_usuario, p.insight_atual, 1 if getattr(p, "teto_manual", False) else 0,
                    float(getattr(p, "custo_medio", 0.0) or 0.0)
                ))
                count += cur.rowcount
        return count

    def save_positions(self, positions: List[Position]) -> int:
        """Alias para persist_positions para compatibilidade de interface."""
        return self.persist_positions(positions)

    def load_positions(self) -> List[Position]:
        """Carrega posições salvas no SQLite para restauração sem Google Sheets."""
        with self._conn() as c:
            rows = c.execute("SELECT * FROM positions ORDER BY margem_seguranca DESC").fetchall()
            positions = []
            for idx, r in enumerate(rows, start=10):
                teto_man = bool(r["teto_manual"]) if "teto_manual" in r.keys() else False
                c_medio = float(r["custo_medio"] or 0.0) if "custo_medio" in r.keys() else 0.0
                q_antes = r["qtd_antes"] or 0
                q_aporte = r["qtd_aporte"] or 0
                p_atual = float(r["preco_atual"] or 0.0)
                positions.append(Position(
                    ticker=r["ticker"],
                    row_index=idx,
                    empresa=r["empresa"] or "",
                    setor=r["setor"] or "",
                    qtd_antes=q_antes,
                    qtd_aporte=q_aporte,
                    qtd_total=q_antes + q_aporte,
                    preco_atual=p_atual,
                    div_proj=float(r["div_proj"] or 0.0),
                    preco_teto=float(r["preco_teto"] or 0.0),
                    margem_seguranca=float(r["margem_seguranca"] or 0.0),
                    valor_total=round((q_antes + q_aporte) * p_atual, 2),
                    total_aporte=round(q_aporte * p_atual, 2),
                    feedback_usuario=r["feedback_usuario"] or "",
                    insight_atual=r["insight_atual"] or "",
                    teto_manual=teto_man,
                    custo_medio=c_medio
                ))
            return positions

    # =========================================================================
    # Preferências de Notificação Proativa
    # =========================================================================

    def seed_default_notification_prefs(self) -> int:
        """Inicializa as preferências padrão de notificação de forma idempotente."""
        inserted = 0
        with self._conn() as c:
            for alert_type, enabled in DEFAULT_NOTIFICATION_PREFS:
                cur = c.execute(
                    "INSERT OR IGNORE INTO notification_preferences (alert_type, enabled) VALUES (?, ?)",
                    (alert_type, enabled),
                )
                inserted += cur.rowcount
        return inserted

    def is_notification_enabled(self, alert_type: str) -> bool:
        """Default = True se a preferência não existir (fail-open, para não quebrar quem nunca configurou)."""
        with self._conn() as c:
            row = c.execute(
                "SELECT enabled FROM notification_preferences WHERE alert_type = ?",
                (alert_type,),
            ).fetchone()
        return True if row is None else bool(row["enabled"])

    def set_notification_enabled(self, alert_type: str, enabled: bool, updated_by: Optional[str] = None) -> None:
        """Ativa ou desativa um tipo de notificação específico."""
        if alert_type not in VALID_ALERT_TYPES:
            raise ValueError(f"alert_type inválido: '{alert_type}'. Válidos: {sorted(VALID_ALERT_TYPES)}")
        with self._conn() as c:
            c.execute("""
                INSERT INTO notification_preferences (alert_type, enabled, updated_at, updated_by)
                VALUES (?, ?, datetime('now'), ?)
                ON CONFLICT(alert_type) DO UPDATE SET
                    enabled    = excluded.enabled,
                    updated_at = datetime('now'),
                    updated_by = excluded.updated_by
            """, (alert_type, 1 if enabled else 0, updated_by))

    def set_all_notifications_enabled(self, enabled: bool, updated_by: Optional[str] = None) -> int:
        """Ativa ou desativa globalmente todos os tipos de notificação."""
        with self._conn() as c:
            cur = c.execute(
                "UPDATE notification_preferences SET enabled = ?, updated_at = datetime('now'), updated_by = ?",
                (1 if enabled else 0, updated_by),
            )
            return cur.rowcount

    def list_notification_preferences(self) -> List[Dict[str, Any]]:
        """Lista todas as preferências de notificação cadastradas."""
        self.seed_default_notification_prefs()
        with self._conn() as c:
            rows = c.execute(
                "SELECT alert_type, enabled, updated_at, updated_by FROM notification_preferences ORDER BY alert_type"
            ).fetchall()
        return [dict(r) for r in rows]

    def should_notify(self, ticker: str, alert_type: str = "margem_20") -> bool:
        """Deduplica notificações: respeita preferências proativas e limita a 1 envio por ticker/tipo por dia."""
        # 1. Preferência primeiro (se desabilitado, NÃO registra no log — não polui o histórico)
        if not self.is_notification_enabled(alert_type):
            return False
        # 2. Dedup diário
        with self._conn() as c:
            row = c.execute(
                "SELECT 1 FROM notification_log WHERE ticker=? AND alert_type=? AND sent_date=date('now')",
                (ticker, alert_type),
            ).fetchone()
            if row:
                return False
            c.execute(
                "INSERT OR IGNORE INTO notification_log (ticker, alert_type, sent_date) VALUES (?, ?, date('now'))",
                (ticker, alert_type),
            )
            return True

    # =========================================================================
    # Scheduler de Execução Automática (Abordagem C - Híbrida)
    # =========================================================================

    def get_scheduler_config(self) -> Dict[str, Any]:
        """Obtém configuração singleton do scheduler (id=1). Cria com valores padrão se não existir."""
        with self._conn() as c:
            row = c.execute("SELECT * FROM scheduler_config WHERE id = 1").fetchone()
            if row is None:
                c.execute(
                    "INSERT INTO scheduler_config (id, days_of_week, hours, minute) VALUES (1, ?, ?, 0)",
                    (DEFAULT_SCHEDULER_DAYS, DEFAULT_SCHEDULER_HOURS),
                )
                row = c.execute("SELECT * FROM scheduler_config WHERE id = 1").fetchone()
        return dict(row)

    def update_scheduler_config(
        self,
        *,
        enabled: Optional[bool] = None,
        days_of_week: Optional[str] = None,
        hours: Optional[str] = None,
        minute: Optional[int] = None,
        updated_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Atualização parcial das configurações do scheduler com validações rigorosas."""
        if days_of_week is not None:
            try:
                parsed = sorted({int(x) for x in str(days_of_week).split(",") if x.strip()})
            except ValueError:
                raise ValueError("days_of_week deve ser CSV de inteiros 1-7")
            if not parsed or any(d < 1 or d > 7 for d in parsed):
                raise ValueError("days_of_week fora de 1..7")
            days_of_week = ",".join(str(d) for d in parsed)

        if hours is not None:
            try:
                parsed_h = sorted({int(x) for x in str(hours).split(",") if x.strip()})
            except ValueError:
                raise ValueError("hours deve ser CSV de inteiros 0-23")
            if not parsed_h or any(h < 0 or h > 23 for h in parsed_h):
                raise ValueError("hours fora de 0..23")
            hours = ",".join(str(h) for h in parsed_h)

        if minute is not None and (minute < 0 or minute > 59):
            raise ValueError("minute deve estar entre 0 e 59")

        self.get_scheduler_config()  # garante linha existente
        with self._conn() as c:
            c.execute("""
                UPDATE scheduler_config
                   SET enabled      = COALESCE(?, enabled),
                       days_of_week = COALESCE(?, days_of_week),
                       hours        = COALESCE(?, hours),
                       minute       = COALESCE(?, minute),
                       updated_at   = datetime('now'),
                       updated_by   = COALESCE(?, updated_by)
                 WHERE id = 1
            """, (
                None if enabled is None else (1 if enabled else 0),
                days_of_week, hours, minute, updated_by,
            ))
        return self.get_scheduler_config()

    def is_run_due(self, now: Optional[datetime] = None) -> Tuple[bool, str]:
        """Retorna (deve_rodar, razão). Sem side-effects de I/O de rede."""
        cfg = self.get_scheduler_config()
        if not cfg["enabled"]:
            return False, "disabled"

        now = now or datetime.now()
        iso_dow = now.isoweekday()  # 1=Seg..7=Dom
        days_set = {int(x) for x in str(cfg["days_of_week"]).split(",") if x.strip()}
        if iso_dow not in days_set:
            return False, "outside_days"

        hours_set = {int(x) for x in str(cfg["hours"]).split(",") if x.strip()}
        if now.hour not in hours_set:
            return False, "outside_hours"

        # Tolerância de 7 min (systemd dispara a cada 5 min, acomoda latência)
        if abs(now.minute - cfg["minute"]) > 7:
            return False, "outside_window"

        # Dedup: se rodou nos últimos 20 min, não roda de novo
        with self._conn() as c:
            if now is not None:
                cutoff = (now - timedelta(minutes=20)).isoformat(sep=" ")
                row = c.execute(
                    "SELECT 1 FROM runs WHERE datetime(timestamp) >= datetime(?) LIMIT 1",
                    (cutoff,),
                ).fetchone()
            else:
                row = c.execute(
                    "SELECT 1 FROM runs WHERE datetime(timestamp) >= datetime('now', '-20 minutes') LIMIT 1"
                ).fetchone()
            if row:
                return False, "already_ran_recently"

        return True, "due"

    def next_scheduled_run(self, now: Optional[datetime] = None) -> Optional[str]:
        """Retorna string ISO do próximo slot programado, ou None se desabilitado."""
        cfg = self.get_scheduler_config()
        if not cfg["enabled"]:
            return None
        now = now or datetime.now()
        hours = sorted({int(x) for x in str(cfg["hours"]).split(",") if x.strip()})
        days = sorted({int(x) for x in str(cfg["days_of_week"]).split(",") if x.strip()})
        if not hours or not days:
            return None

        minute = cfg.get("minute", 0) or 0
        for delta_days in range(8):
            d = now + timedelta(days=delta_days)
            if d.isoweekday() not in days:
                continue
            for h in hours:
                candidate = d.replace(hour=h, minute=minute, second=0, microsecond=0)
                if candidate > now:
                    return candidate.isoformat()
        return None

    def save_portfolio_snapshot(
        self,
        data_snapshot: str,
        patrimonio_total: float,
        cotas_totais: int,
        dividendos_mes: float = 0.0,
        aporte_planejado: float = 0.0,
        rentabilidade_mes: float = 0.0,
        setores_json: Optional[str] = None
    ) -> None:
        """Salva ou atualiza o snapshot de patrimônio para uma data (YYYY-MM-DD)."""
        with self._conn() as c:
            c.execute("""
                INSERT INTO portfolio_snapshots 
                (data_snapshot, patrimonio_total, cotas_totais, dividendos_mes, aporte_planejado, rentabilidade_mes, setores_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(data_snapshot) DO UPDATE SET
                    patrimonio_total = excluded.patrimonio_total,
                    cotas_totais = excluded.cotas_totais,
                    dividendos_mes = excluded.dividendos_mes,
                    aporte_planejado = excluded.aporte_planejado,
                    rentabilidade_mes = excluded.rentabilidade_mes,
                    setores_json = COALESCE(excluded.setores_json, portfolio_snapshots.setores_json),
                    created_at = datetime('now')
            """, (data_snapshot, patrimonio_total, cotas_totais, dividendos_mes, aporte_planejado, rentabilidade_mes, setores_json or '{}'))

    def get_portfolio_snapshots(self, limit_months: int = 12) -> List[Dict[str, Any]]:
        """Retorna histórico de snapshots ordenados por data."""
        with self._conn() as c:
            rows = c.execute("""
                SELECT data_snapshot, patrimonio_total, cotas_totais, dividendos_mes, aporte_planejado, rentabilidade_mes, setores_json
                FROM portfolio_snapshots
                ORDER BY data_snapshot ASC
                LIMIT ?
            """, (limit_months,)).fetchall()
            result = []
            for r in rows:
                item = dict(r)
                if item.get("setores_json"):
                    try:
                        item["setores"] = json.loads(item["setores_json"])
                    except Exception:
                        item["setores"] = {}
                else:
                    item["setores"] = {}
                result.append(item)
            return result

    def seed_initial_snapshots_if_empty(
        self,
        current_patrimonio: float,
        current_cotas: int,
        setores_dict: Optional[Dict[str, float]] = None
    ) -> None:
        """Gera histórico inicial coerente dos últimos 6 meses com datas dinâmicas se a tabela tiver poucos registros."""
        with self._conn() as c:
            count = c.execute("SELECT COUNT(*) FROM portfolio_snapshots").fetchone()[0]
            if count >= 6 or current_patrimonio <= 0:
                return
            
            hoje = datetime.now()
            setores_base = setores_dict or {}
            factors = DEFAULT_SYNTHETIC_CURVE_FACTORS
            for i, factor in enumerate(factors):
                months_ago = 5 - i
                year = hoje.year
                month = hoje.month - months_ago
                while month <= 0:
                    month += 12
                    year -= 1
                
                if months_ago == 0:
                    dt_str = hoje.strftime("%Y-%m-%d")
                else:
                    last_day = calendar.monthrange(year, month)[1]
                    dt_str = f"{year:04d}-{month:02d}-{last_day:02d}"
                
                pat = round(current_patrimonio * factor, 2)
                cotas = int(current_cotas * factor)
                div = round(pat * 0.006, 2)
                ap = round(800.0 + (i * 40.0), 2)
                
                month_setores = {k: round(v * factor, 2) for k, v in setores_base.items()} if setores_base else {}
                sec_json = json.dumps(month_setores, ensure_ascii=False)
                
                c.execute("""
                    INSERT INTO portfolio_snapshots 
                    (data_snapshot, patrimonio_total, cotas_totais, dividendos_mes, aporte_planejado, rentabilidade_mes, setores_json)
                    VALUES (?, ?, ?, ?, ?, 0.0, ?)
                    ON CONFLICT(data_snapshot) DO UPDATE SET
                        patrimonio_total = excluded.patrimonio_total,
                        cotas_totais = excluded.cotas_totais,
                        setores_json = COALESCE(excluded.setores_json, portfolio_snapshots.setores_json)
                """, (dt_str, pat, cotas, div, ap, sec_json))

    def update_insight_feedback(self, ticker: str, feedback: str) -> None:
        """Atualiza o feedback do usuário na tabela positions e no último registro de historico_analises."""
        with self._conn() as c:
            c.execute("UPDATE positions SET feedback_usuario = ?, updated_at = datetime('now') WHERE ticker = ?", (feedback, ticker))
            try:
                c.execute("""
                    UPDATE historico_analises 
                    SET feedback_usuario = ? 
                    WHERE id = (
                        SELECT id FROM historico_analises WHERE ticker = ? ORDER BY id DESC LIMIT 1
                    )
                """, (feedback, ticker))
            except sqlite3.OperationalError:
                pass

    def save_price_history(self, ticker: str, data_pregao: str, preco_fechamento: float, volume: float = 0.0) -> None:
        """Salva a cotação histórica de fechamento de um pregão."""
        with self._conn() as c:
            c.execute("""
                INSERT INTO price_history (ticker, data_pregao, preco_fechamento, volume)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(ticker, data_pregao) DO UPDATE SET
                    preco_fechamento = excluded.preco_fechamento,
                    volume = excluded.volume
            """, (ticker, data_pregao, preco_fechamento, volume))

    def get_price_history(self, ticker: str, limit_days: int = 90) -> List[Dict[str, Any]]:
        """Retorna as cotações históricas dos últimos N pregões."""
        with self._conn() as c:
            rows = c.execute("""
                SELECT ticker, data_pregao, preco_fechamento, volume
                FROM price_history
                WHERE ticker = ?
                ORDER BY data_pregao ASC
                LIMIT ?
            """, (ticker, limit_days)).fetchall()
            return [dict(r) for r in rows]

    def sync_yfinance_history(self, tickers: Optional[List[str]] = None) -> int:
        """Baixa o histórico diário real via yfinance e persiste no SQLite."""
        if not tickers:
            positions = self.load_positions()
            tickers = [p.ticker for p in positions] if positions else [
                "BBAS3", "SAPR4", "BBSE3", "MXRF11", "PETR4", "KNCR11", "ITUB4", "HGCR11", "XPLG11", "CMIG4"
            ]

        import yfinance as yf
        total_records = 0
        with self._conn() as c:
            for t in tickers:
                symbol = f"{t}.SA" if not t.endswith(".SA") else t
                try:
                    stock = yf.Ticker(symbol)
                    hist = stock.history(period="1y", timeout=15)
                    if hist is None or hist.empty:
                        continue
                    clean_ticker = t.replace(".SA", "")
                    for dt, row in hist.iterrows():
                        dt_str = dt.strftime("%Y-%m-%d")
                        close_price = round(float(row["Close"]), 2)
                        vol = float(row.get("Volume", 0.0))
                        c.execute("""
                            INSERT INTO price_history (ticker, data_pregao, preco_fechamento, volume)
                            VALUES (?, ?, ?, ?)
                            ON CONFLICT(ticker, data_pregao) DO UPDATE SET
                                preco_fechamento = excluded.preco_fechamento,
                                volume = excluded.volume
                        """, (clean_ticker, dt_str, close_price, vol))
                        total_records += 1
                except Exception as e:
                    logger.warn("yfinance_history_sync_error", ticker=t, error=str(e))
        return total_records

    def build_real_portfolio_snapshots(self, limit_months: int = 12) -> List[Dict[str, Any]]:
        """Gera a curva patrimonial real dos últimos N meses combinando cotas e preços reais do pregão."""
        positions = self.load_positions()
        if not positions:
            return self.get_portfolio_snapshots(limit_months)

        soma_pat = sum((p.qtd_antes or 0) * (p.preco_atual or 0.0) for p in positions)
        soma_cotas = sum(p.qtd_antes or 0 for p in positions)
        setores_base = {}
        for p in positions:
            sec = p.setor or "B.E.S.T."
            setores_base[sec] = round(setores_base.get(sec, 0.0) + (p.qtd_antes or 0) * (p.preco_atual or 0.0), 2)

        with self._conn() as c:
            # Obtém as datas de fechamento mensal presentes no price_history
            dates = c.execute("""
                SELECT DISTINCT strftime('%Y-%m', data_pregao) as ym, MAX(data_pregao) as end_date
                FROM price_history
                GROUP BY ym
                ORDER BY end_date ASC
                LIMIT ?
            """, (limit_months,)).fetchall()

            if not dates or len(dates) < 3:
                self.seed_initial_snapshots_if_empty(round(soma_pat, 2), soma_cotas, setores_base)
                return self.get_portfolio_snapshots(limit_months)

            # Quando há histórico real suficiente (>= 3 meses), limpa snapshots anteriores para reconstrução canônica
            c.execute("DELETE FROM portfolio_snapshots")

            for d in dates:
                dt_str = d["end_date"]
                soma_pat_m = 0.0
                soma_cotas_m = 0
                month_setores = {}
                for p in positions:
                    row_p = c.execute(
                        "SELECT preco_fechamento FROM price_history WHERE ticker = ? AND data_pregao <= ? ORDER BY data_pregao DESC LIMIT 1",
                        (p.ticker, dt_str)
                    ).fetchone()
                    preco = float(row_p["preco_fechamento"]) if row_p else p.preco_atual
                    val = (p.qtd_antes or 0) * preco
                    soma_pat_m += val
                    soma_cotas_m += (p.qtd_antes or 0)
                    sec = p.setor or "B.E.S.T."
                    month_setores[sec] = round(month_setores.get(sec, 0.0) + val, 2)

                if soma_pat_m > 0:
                    c.execute("""
                        INSERT INTO portfolio_snapshots 
                        (data_snapshot, patrimonio_total, cotas_totais, dividendos_mes, aporte_planejado, rentabilidade_mes, setores_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(data_snapshot) DO UPDATE SET
                            patrimonio_total = excluded.patrimonio_total,
                            cotas_totais = excluded.cotas_totais,
                            dividendos_mes = excluded.dividendos_mes,
                            aporte_planejado = excluded.aporte_planejado,
                            rentabilidade_mes = excluded.rentabilidade_mes,
                            setores_json = COALESCE(excluded.setores_json, portfolio_snapshots.setores_json),
                            created_at = datetime('now')
                    """, (
                        dt_str,
                        round(soma_pat_m, 2),
                        soma_cotas_m,
                        round(soma_pat_m * 0.009, 2),
                        1000.0,
                        0.0,
                        json.dumps(month_setores, ensure_ascii=False)
                    ))

            rows = c.execute("""
                SELECT * FROM portfolio_snapshots 
                ORDER BY data_snapshot ASC 
                LIMIT ?
            """, (limit_months,)).fetchall()

            snaps = []
            for r in rows:
                item = dict(r)
                if item.get("setores_json"):
                    try:
                        item["setores"] = json.loads(item["setores_json"])
                    except Exception:
                        item["setores"] = {}
                else:
                    item["setores"] = {}
                snaps.append(item)
            return snaps

    # ------------------------------------------------------------------ #
    # Outbox Pattern: Writes pendentes para o Google Sheets
    # ------------------------------------------------------------------ #
    def record_pending_sheets_write(self, ticker: str, payload: Dict[str, Any]) -> int:
        """Enfileira uma escrita para o Google Sheets quando a rede ou API estiver indisponível."""
        payload_str = json.dumps(payload, ensure_ascii=False)
        with self._conn() as c:
            cur = c.execute("""
                INSERT INTO pending_sheets_writes (ticker, payload_json)
                VALUES (?, ?)
            """, (ticker, payload_str))
            logger.info("pending_sheets_write_recorded", ticker=ticker, write_id=cur.lastrowid)
            return cur.lastrowid

    def get_pending_sheets_writes(self) -> List[Dict[str, Any]]:
        """Retorna todas as escritas pendentes para o Google Sheets."""
        with self._conn() as c:
            rows = c.execute("""
                SELECT id, ticker, payload_json, created_at, retry_count
                  FROM pending_sheets_writes
                 ORDER BY id ASC
            """).fetchall()
            result = []
            for r in rows:
                item = dict(r)
                try:
                    item["payload"] = json.loads(item["payload_json"])
                except Exception:
                    item["payload"] = {}
                result.append(item)
            return result

    def clear_pending_sheets_write(self, write_id: int) -> None:
        """Remove escrita pendente após sucesso no espelhamento para o Sheets."""
        with self._conn() as c:
            c.execute("DELETE FROM pending_sheets_writes WHERE id = ?", (write_id,))

    def increment_pending_sheets_retry(self, write_id: int) -> None:
        """Incrementa contador de tentativas de escrita pendente."""
        with self._conn() as c:
            c.execute("UPDATE pending_sheets_writes SET retry_count = retry_count + 1 WHERE id = ?", (write_id,))

    # ------------------------------------------------------------------ #
    # Cache Persistente em SQLite das posições do Google Sheets
    # ------------------------------------------------------------------ #
    def save_sheets_positions_cache(self, positions: List[Position], ttl_seconds: int = 60) -> None:
        """Salva posições do Sheets em cache SQLite com validade definida."""
        expires_at = (datetime.now() + timedelta(seconds=ttl_seconds)).isoformat()
        with self._conn() as c:
            for p in positions:
                data = {
                    "ticker": p.ticker,
                    "row_index": p.row_index,
                    "empresa": p.empresa,
                    "setor": p.setor,
                    "qtd_antes": p.qtd_antes,
                    "qtd_aporte": p.qtd_aporte,
                    "preco_atual": p.preco_atual,
                    "div_proj": p.div_proj,
                    "preco_teto": p.preco_teto,
                    "margem_seguranca": p.margem_seguranca,
                    "feedback_usuario": p.feedback_usuario,
                    "insight_atual": p.insight_atual
                }
                c.execute("""
                    INSERT INTO sheets_positions_cache (ticker, payload_json, expires_at, updated_at)
                    VALUES (?, ?, ?, datetime('now'))
                    ON CONFLICT(ticker) DO UPDATE SET
                        payload_json = excluded.payload_json,
                        expires_at = excluded.expires_at,
                        updated_at = datetime('now')
                """, (p.ticker, json.dumps(data, ensure_ascii=False), expires_at))

    def get_sheets_positions_cache(self) -> Tuple[Optional[List[Position]], bool]:
        """
        Retorna posições salvas no cache do Sheets em SQLite.
        Retorno: (lista_posicoes, is_stale)
        """
        with self._conn() as c:
            rows = c.execute("SELECT ticker, payload_json, expires_at FROM sheets_positions_cache").fetchall()
            if not rows:
                return None, True
            
            now_iso = datetime.now().isoformat()
            is_stale = False
            positions = []
            for r in rows:
                if r["expires_at"] < now_iso:
                    is_stale = True
                try:
                    d = json.loads(r["payload_json"])
                    positions.append(Position(
                        ticker=d["ticker"],
                        row_index=d.get("row_index", 10),
                        empresa=d.get("empresa", ""),
                        setor=d.get("setor", ""),
                        qtd_antes=d.get("qtd_antes", 0),
                        qtd_aporte=d.get("qtd_aporte", 0),
                        preco_atual=float(d.get("preco_atual", 0.0)),
                        div_proj=float(d.get("div_proj", 0.0)),
                        preco_teto=float(d.get("preco_teto", 0.0)),
                        margem_seguranca=float(d.get("margem_seguranca", 0.0)),
                        feedback_usuario=d.get("feedback_usuario", ""),
                        insight_atual=d.get("insight_atual", "")
                    ))
                except Exception:
                    pass
            return (positions if positions else None), is_stale

    # ------------------------------------------------------------------ #
    # Rastreamento de Dividendos Reais (P0: dividends_received)
    # ------------------------------------------------------------------ #
    def record_dividend_received(
        self,
        ticker: str,
        data_pagamento: str,
        valor_total: float,
        valor_por_cota: float = 0.0,
        quantidade: int = 0,
        tipo: str = "DIVIDENDO",
        fonte: str = "manual"
    ) -> int:
        """Registra um dividendo real recebido no SQLite."""
        ticker = ticker.strip().upper()
        mes_ano = data_pagamento[:7] if len(data_pagamento) >= 7 else datetime.now().strftime("%Y-%m")
        with self._conn() as c:
            cur = c.execute("""
                INSERT INTO dividends_received (
                    ticker, data_pagamento, mes_ano, valor_total, valor_por_cota,
                    quantidade, tipo, fonte, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """, (ticker, data_pagamento, mes_ano, valor_total, valor_por_cota, quantidade, tipo, fonte))
            return cur.lastrowid

    def list_dividends_received(
        self,
        ticker: Optional[str] = None,
        mes_ano: Optional[str] = None,
        year: Optional[int] = None,
        limit: int = 100
    ) -> List[Dict[str, Any]]:
        """Lista registros de proventos reais recebidos com filtros flexíveis."""
        query = "SELECT * FROM dividends_received WHERE 1=1"
        params = []
        if ticker:
            query += " AND ticker = ?"
            params.append(ticker.strip().upper())
        if mes_ano:
            query += " AND mes_ano = ?"
            params.append(mes_ano)
        if year:
            query += " AND mes_ano LIKE ?"
            params.append(f"{year}-%")
        query += " ORDER BY data_pagamento DESC, id DESC LIMIT ?"
        params.append(limit)

        with self._conn() as c:
            rows = c.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def get_dividends_summary_by_month(self, months: int = 12) -> List[Dict[str, Any]]:
        """Retorna proventos agrupados por mês para os últimos N meses."""
        with self._conn() as c:
            rows = c.execute("""
                SELECT mes_ano, SUM(valor_total) as total_mes, COUNT(*) as qtd_lancamentos
                FROM dividends_received
                GROUP BY mes_ano
                ORDER BY mes_ano DESC
                LIMIT ?
            """, (months,)).fetchall()
            results = [dict(r) for r in rows]
            results.reverse()
            return results

    def get_total_dividends_received(self, year: Optional[int] = None) -> float:
        """Retorna o valor total de dividendos recebidos no ano ou histórico."""
        query = "SELECT COALESCE(SUM(valor_total), 0.0) FROM dividends_received WHERE 1=1"
        params = []
        if year:
            query += " AND mes_ano LIKE ?"
            params.append(f"{year}-%")
        with self._conn() as c:
            val = c.execute(query, params).fetchone()[0]
            return round(float(val), 2)

    # ------------------------------------------------------------------ #
    # Orçamento & Transações Conciliadas (Onda 2)
    # ------------------------------------------------------------------ #
    def list_transacoes(
        self,
        mes_ano: Optional[str] = None,
        categoria: Optional[str] = None,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Retorna transações bancárias/cartão persistidas com filtros."""
        query = "SELECT * FROM transacoes WHERE 1=1"
        params = []
        if mes_ano:
            query += " AND mes_ano = ?"
            params.append(mes_ano)
        if categoria:
            query += " AND categoria = ?"
            params.append(categoria)
        query += " ORDER BY data DESC, id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self._conn() as c:
            rows = c.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def get_budget_overview(self, mes_ano: Optional[str] = None) -> Dict[str, Any]:
        """Calcula a visão consolidada de Orçamento (Previsto vs Realizado) do mês."""
        if not mes_ano:
            mes_ano = datetime.now().strftime("%Y-%m")

        recurring = self.get_all_expenses(mes_ano)
        with self._conn() as c:
            # Gastos reais por categoria no mês via tabela transacoes
            rows = c.execute("""
                SELECT categoria,
                       SUM(CASE WHEN tipo = 'DEBIT' THEN valor ELSE 0 END) as total_debito,
                       SUM(CASE WHEN tipo = 'CREDIT' THEN valor ELSE 0 END) as total_credito
                FROM transacoes
                WHERE mes_ano = ?
                GROUP BY categoria
            """, (mes_ano,)).fetchall()
            realizado_map = {r["categoria"]: float(r["total_debito"] or 0.0) for r in rows}
            creditos_map = {r["categoria"]: float(r["total_credito"] or 0.0) for r in rows}

        total_entradas_real = sum(c for cat, c in creditos_map.items() if "SALÁRIO" in cat.upper() or "RECEITA" in cat.upper() or "ENTRADA" in cat.upper())
        total_saidas_real = sum(realizado_map.values())
        
        # Consolida itens comparativos
        all_cats = sorted(set(list(recurring.keys()) + list(realizado_map.keys())))
        items = []
        for cat in all_cats:
            previsto = recurring.get(cat, 0.0)
            realizado = realizado_map.get(cat, 0.0)
            delta = round(realizado - previsto, 2)
            items.append({
                "categoria": cat,
                "previsto": previsto,
                "realizado": realizado,
                "delta": delta,
                "status": "CONFORME" if abs(delta) < 1.0 else ("ECONOMIA" if delta < 0 else "ATENÇÃO")
            })

        salario_previsto = self.get_expense_amount(mes_ano, "Salário / Renda Principal")
        total_entradas = total_entradas_real if total_entradas_real > 0 else salario_previsto

        return {
            "mes_ano": mes_ano,
            "total_entradas": round(total_entradas, 2),
            "total_saidas": round(total_saidas_real, 2),
            "saldo_livre": round(total_entradas - total_saidas_real, 2),
            "items": items
        }

