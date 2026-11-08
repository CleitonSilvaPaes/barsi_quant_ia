"""Repositório de orçamento, despesas recorrentes e transações OFX persistidas.

Esta é a ÚNICA fonte da verdade para valores monetários mensais.
Regra permanente: NENHUM valor monetário mensal pode ser hardcoded no código.
Se o valor não existe em `recurring_expenses`, ele NÃO EXISTE.
"""
from __future__ import annotations

import os
import sqlite3
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
        self.seed_default_keywords()

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
                        feedback_usuario, insight_atual, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
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
                        updated_at = datetime('now')
                """, (
                    p.ticker, p.empresa, p.setor, p.qtd_antes, p.qtd_aporte,
                    p.preco_atual, p.div_proj, p.preco_teto, p.margem_seguranca,
                    p.feedback_usuario, p.insight_atual
                ))
                count += cur.rowcount
        return count

    def load_positions(self) -> List[Position]:
        """Carrega posições salvas no SQLite para restauração sem Google Sheets."""
        with self._conn() as c:
            rows = c.execute("SELECT * FROM positions ORDER BY margem_seguranca DESC").fetchall()
            positions = []
            for idx, r in enumerate(rows, start=10):
                positions.append(Position(
                    ticker=r["ticker"],
                    row_index=idx,
                    empresa=r["empresa"] or "",
                    setor=r["setor"] or "",
                    qtd_antes=r["qtd_antes"] or 0,
                    qtd_aporte=r["qtd_aporte"] or 0,
                    preco_atual=float(r["preco_atual"] or 0.0),
                    div_proj=float(r["div_proj"] or 0.0),
                    preco_teto=float(r["preco_teto"] or 0.0),
                    margem_seguranca=float(r["margem_seguranca"] or 0.0),
                    feedback_usuario=r["feedback_usuario"] or "",
                    insight_atual=r["insight_atual"] or ""
                ))
            return positions

    def should_notify(self, ticker: str, alert_type: str = "margem_20") -> bool:
        """Deduplica notificações: retorna True se ainda não notificou este ticker/tipo hoje, e registra o envio."""
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
