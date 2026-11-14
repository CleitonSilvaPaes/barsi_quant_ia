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
from barsi.adapters.history.schema import (
    INIT_BUDGET_SCHEMA_SQL,
    DEFAULT_KEYWORDS,
    DEFAULT_BUDGET_SEEDS,
    DEFAULT_SYNTHETIC_CURVE_FACTORS,
    DEFAULT_SCHEDULER_DAYS,
    DEFAULT_SCHEDULER_HOURS,
    DEFAULT_NOTIFICATION_PREFS,
    VALID_ALERT_TYPES,
    VALID_CHART_TYPES,
    VALID_DATA_SOURCES,
    DEFAULT_GOAL_SEEDS,
)


@dataclass(frozen=True)
class RecurringExpense:
    category: str
    amount: float
    valid_from: str
    valid_to: Optional[str]
    source: str


def resolve_db_path(path: Optional[str] = None) -> str:
    """Resolve o caminho do SQLite com segurança para execução na raiz ou em subpastas."""
    from barsi.config import settings

    target = path if path is not None else settings.sqlite_db_path
    if os.path.isabs(target):
        return target
    if os.path.exists(target):
        return os.path.abspath(target)

    # Procura em subdiretório barsi_quant_ia
    sub_path = os.path.join("barsi_quant_ia", target.lstrip("./"))
    if os.path.exists(sub_path):
        return os.path.abspath(sub_path)

    # Procura no diretório pai
    parent_path = os.path.join("..", target.lstrip("./"))
    if os.path.exists(parent_path):
        return os.path.abspath(parent_path)

    return os.path.abspath(target)


def validate_target_month(target_month: Optional[str]) -> Optional[str]:
    """Valida e normaliza o parâmetro target_month.

    Formato esperado: 'YYYY-MM' (ex: '2026-11').
    Faixa aceita: Ano entre 2020 e 2100, Mês entre 01 e 12.
    Retorna None se target_month for nulo ou vazio.
    Levanta ValueError se o formato ou a data forem inválidos.
    """
    if target_month is None:
        return None
    val = target_month.strip()
    if not val:
        return None
    import re

    if not re.match(r"^\d{4}-\d{2}$", val):
        raise ValueError(f"target_month inválido: '{val}'. Formato esperado: YYYY-MM (ex: 2026-11).")
    ano, mes = int(val[:4]), int(val[5:7])
    if not (2020 <= ano <= 2100) or not (1 <= mes <= 12):
        raise ValueError(
            f"target_month fora de faixa: '{val}'. O ano deve estar entre 2020 e 2100 e mês entre 01 e 12."
        )
    return val


class BudgetRepository:
    """Leitura e escrita de orçamento no SQLite. Sem I/O de rede, sem dependência do Google Sheets."""

    def __init__(self, db_path: Optional[str] = None, user_id: int = 1):
        self.db_path = resolve_db_path(db_path)
        self.user_id = int(user_id or 1)
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
                # Migrações incrementais seguras e multi-tenancy
                cols_snap = [r["name"] for r in conn.execute("PRAGMA table_info(portfolio_snapshots)").fetchall()]
                if "setores_json" not in cols_snap:
                    conn.execute("ALTER TABLE portfolio_snapshots ADD COLUMN setores_json TEXT DEFAULT '{}'")
                if "user_id" not in cols_snap:
                    conn.execute("ALTER TABLE portfolio_snapshots ADD COLUMN user_id INTEGER DEFAULT 1")

                cols_pos = [r["name"] for r in conn.execute("PRAGMA table_info(positions)").fetchall()]
                if "teto_manual" not in cols_pos:
                    conn.execute("ALTER TABLE positions ADD COLUMN teto_manual INTEGER DEFAULT 0")
                if "custo_medio" not in cols_pos:
                    conn.execute("ALTER TABLE positions ADD COLUMN custo_medio REAL DEFAULT 0.0")
                if "user_id" not in cols_pos:
                    conn.execute("ALTER TABLE positions ADD COLUMN user_id INTEGER DEFAULT 1")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_positions_user_id ON positions(user_id)")

                cols_goals = [r["name"] for r in conn.execute("PRAGMA table_info(financial_goals)").fetchall()]
                if "user_id" not in cols_goals:
                    conn.execute("ALTER TABLE financial_goals ADD COLUMN user_id INTEGER DEFAULT 1")

                cols_tx = [r["name"] for r in conn.execute("PRAGMA table_info(transacoes)").fetchall()]
                if "user_id" not in cols_tx:
                    conn.execute("ALTER TABLE transacoes ADD COLUMN user_id INTEGER DEFAULT 1")

                cols_rec = [r["name"] for r in conn.execute("PRAGMA table_info(recurring_expenses)").fetchall()]
                if "user_id" not in cols_rec:
                    conn.execute("ALTER TABLE recurring_expenses ADD COLUMN user_id INTEGER DEFAULT 1")

                cols_divs = [r["name"] for r in conn.execute("PRAGMA table_info(dividends_received)").fetchall()]
                if "user_id" not in cols_divs:
                    conn.execute("ALTER TABLE dividends_received ADD COLUMN user_id INTEGER DEFAULT 1")
            except Exception:
                pass
        self.seed_default_categories()
        self.seed_default_keywords()
        self.seed_default_notification_prefs()
        self.seed_default_goals()

    DEFAULT_EXPENSE_CATEGORIES = [
        ("Moradia", "fixa"),
        ("Alimentação", "variavel"),
        ("Transporte", "variavel"),
        ("Saúde", "variavel"),
        ("Educação", "fixa"),
        ("Lazer", "variavel"),
        ("Tributos & Taxas", "tributo"),
        ("Reserva / RDB", "reserva"),
        ("Investimentos", "investimento"),
        ("Salário / Proventos", "entrada"),
        ("Despesas Variáveis", "variavel"),
        ("Cooperativa Ailos", "fixa"),
    ]

    def seed_default_categories(self) -> int:
        """Garante que as categorias de despesa padrão existam de forma idempotente."""
        inserted = 0
        with self._conn() as c:
            for name, kind in self.DEFAULT_EXPENSE_CATEGORIES:
                cur = c.execute(
                    """
                    INSERT OR IGNORE INTO expense_categories (name, kind, active)
                    VALUES (?, ?, 1)
                """,
                    (name, kind),
                )
                inserted += cur.rowcount
        return inserted

    def list_categories(self, active_only: bool = False) -> List[Dict[str, Any]]:
        """Retorna todas as categorias de despesa cadastradas."""
        query = "SELECT id, name, kind, active, created_at FROM expense_categories"
        if active_only:
            query += " WHERE active = 1"
        query += " ORDER BY name ASC"
        with self._conn() as c:
            rows = c.execute(query).fetchall()
            return [dict(r) for r in rows]

    def create_category(self, name: str, kind: str = "variavel") -> Dict[str, Any]:
        """Cria uma nova categoria de despesa."""
        name = name.strip()
        kind = kind.strip().lower()
        valid_kinds = {"fixa", "variavel", "tributo", "reserva", "investimento", "entrada"}
        if kind not in valid_kinds:
            kind = "variavel"
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO expense_categories (name, kind, active)
                VALUES (?, ?, 1)
            """,
                (name, kind),
            )
            row = c.execute(
                "SELECT id, name, kind, active, created_at FROM expense_categories WHERE name = ?", (name,)
            ).fetchone()
            return dict(row)

    def update_category(self, category_id: int, name: str, kind: str, active: bool = True) -> bool:
        """Atualiza nome, tipo ou status de uma categoria."""
        name = name.strip()
        kind = kind.strip().lower()
        valid_kinds = {"fixa", "variavel", "tributo", "reserva", "investimento", "entrada"}
        if kind not in valid_kinds:
            kind = "variavel"
        with self._conn() as c:
            cur = c.execute(
                """
                UPDATE expense_categories
                   SET name = ?, kind = ?, active = ?
                 WHERE id = ?
            """,
                (name, kind, 1 if active else 0, category_id),
            )
            return cur.rowcount > 0

    def delete_category(self, category_id: int) -> bool:
        """Exclui uma categoria de despesa pelo ID."""
        with self._conn() as c:
            cur = c.execute("DELETE FROM expense_categories WHERE id = ?", (category_id,))
            return cur.rowcount > 0

    def seed_default_keywords(self) -> int:
        """Popula a tabela category_keywords de forma estritamente idempotente."""
        inserted = 0
        with self._conn() as c:
            for kw, cat, bucket, prio in DEFAULT_KEYWORDS:
                cur = c.execute(
                    """
                    INSERT OR IGNORE INTO category_keywords (keyword, category, bucket, priority)
                    VALUES (?, ?, ?, ?)
                """,
                    (kw, cat, bucket, prio),
                )
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

    def seed_defaults(self, seeds: Optional[List[Tuple[str, float, str, str]]] = None) -> int:
        """Popula as despesas recorrentes padrão de forma 100% idempotente.

        Prioridade de carregamento:
        1. Parâmetro explícito `seeds`.
        2. Arquivo JSON em `BARSI_SEEDS_JSON` ou `memory/seeds_local.json`.
        3. Constante `DEFAULT_BUDGET_SEEDS` como fallback sintético.
        """
        if seeds is None:
            custom_path = os.getenv("BARSI_SEEDS_JSON") or os.path.join(
                os.path.dirname(self.db_path), "seeds_local.json"
            )
            if os.path.exists(custom_path):
                try:
                    with open(custom_path, "r", encoding="utf-8") as f:
                        loaded = json.load(f)
                        if isinstance(loaded, list):
                            seeds = [(str(item[0]), float(item[1]), str(item[2]), str(item[3])) for item in loaded]
                except Exception as exc:
                    logger.warning("failed_loading_custom_seeds", path=custom_path, error=str(exc))

        target_seeds = seeds if seeds is not None else DEFAULT_BUDGET_SEEDS
        for cat, amt, dt, kind in target_seeds:
            self.upsert_recurring(cat, amt, dt, kind=kind, source="seed")
        return len(target_seeds)

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
            row = c.execute(
                """
                SELECT be.amount
                  FROM budget_entries be
                  JOIN expense_categories ec ON ec.id = be.category_id
                 WHERE ec.name = ? AND be.mes_ano = ?
                 LIMIT 1
            """,
                (category, mes_ano),
            ).fetchone()
            if row:
                return float(row["amount"])

            # 2. Recorrente vigente
            mes_iso = f"{mes_ano}-01"
            row = c.execute(
                """
                SELECT re.amount
                  FROM recurring_expenses re
                  JOIN expense_categories ec ON ec.id = re.category_id
                 WHERE ec.name = ?
                   AND re.valid_from <= ?
                   AND (re.valid_to IS NULL OR re.valid_to >= ?)
                 ORDER BY re.valid_from DESC
                 LIMIT 1
            """,
                (category, mes_iso, mes_iso),
            ).fetchone()
            if row:
                return float(row["amount"])

            return 0.0

    def get_all_expenses(self, mes_ano: str) -> Dict[str, float]:
        """Retorna {categoria: valor} para um mês em 1 query consolidada."""
        mes_iso = f"{mes_ano}-01"
        res: Dict[str, float] = {}
        with self._conn() as c:
            # 1. Despesas recorrentes vigentes
            rows_recurring = c.execute(
                """
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
            """,
                (mes_iso, mes_iso),
            ).fetchall()
            for r in rows_recurring:
                res[r["name"]] = float(r["amount"])

            # 2. Snapshot conciliado do mês (budget_entries) sobrepõe (dado real > estimado)
            rows_snapshot = c.execute(
                """
                SELECT ec.name, be.amount
                  FROM budget_entries be
                  JOIN expense_categories ec ON ec.id = be.category_id
                 WHERE be.mes_ano = ?
            """,
                (mes_ano,),
            ).fetchall()
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
            c.execute(
                """INSERT OR IGNORE INTO expense_categories (name, kind)
                         VALUES (?, ?)""",
                (category, kind),
            )
            row = c.execute("SELECT id FROM expense_categories WHERE name = ?", (category,)).fetchone()
            cat_id = row["id"]

            # Fecha vigência anterior aberta (apenas se for anterior à data informada)
            c.execute(
                """
                UPDATE recurring_expenses
                   SET valid_to = date(?, '-1 day')
                 WHERE category_id = ? AND valid_to IS NULL AND valid_from < ?
            """,
                (valid_from, cat_id, valid_from),
            )

            c.execute(
                """
                INSERT INTO recurring_expenses
                    (category_id, amount, valid_from, valid_to, source, notes)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(category_id, valid_from) DO UPDATE SET
                    amount = excluded.amount,
                    valid_to = excluded.valid_to,
                    source = excluded.source,
                    notes = excluded.notes
            """,
                (cat_id, amount, valid_from, valid_to, source, notes),
            )
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
                fitid = (
                    getattr(t, "id_transacao", None)
                    or getattr(t, "fitid", None)
                    or (t.get("id_transacao") or t.get("fitid") if isinstance(t, dict) else "")
                )
                data = getattr(t, "data", None) or (t.get("data") if isinstance(t, dict) else "")
                tipo = getattr(t, "tipo", None) or (t.get("tipo") if isinstance(t, dict) else "")
                valor = (
                    getattr(t, "valor", 0.0)
                    if hasattr(t, "valor")
                    else (t.get("valor", 0.0) if isinstance(t, dict) else 0.0)
                )
                descricao = getattr(t, "descricao", None) or (t.get("descricao") if isinstance(t, dict) else "")
                categoria = getattr(t, "categoria", None) or (
                    t.get("categoria", "Outros") if isinstance(t, dict) else "Outros"
                )
                ticker = getattr(t, "ticker_relacionado", None) or (
                    t.get("ticker_relacionado") if isinstance(t, dict) else None
                )
                origem = getattr(t, "origem", None) or (t.get("origem", "CONTA") if isinstance(t, dict) else "CONTA")

                cat_row = c.execute("SELECT id FROM expense_categories WHERE name = ?", (categoria,)).fetchone()
                cat_id = cat_row["id"] if cat_row else None

                cur = c.execute(
                    """
                    INSERT OR IGNORE INTO transacoes
                        (fitid, data, mes_ano, tipo, valor, descricao,
                         categoria, ticker, origem, categoria_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (fitid, data, data[:7] if data else "", tipo, valor, descricao, categoria, ticker, origem, cat_id),
                )
                inserted += cur.rowcount
        logger.info("transacoes_persisted", inserted=inserted)
        return inserted

    def record_budget_entry(self, mes_ano: str, category: str, amount: float, source: str = "ofx") -> None:
        """Registra snapshot mensal de orçamento."""
        with self._conn() as c:
            c.execute("""INSERT OR IGNORE INTO expense_categories (name, kind) VALUES (?, 'variavel')""", (category,))
            cat_row = c.execute("SELECT id FROM expense_categories WHERE name = ?", (category,)).fetchone()
            cat_id = cat_row["id"]

            c.execute(
                """
                INSERT INTO budget_entries (mes_ano, category_id, amount, source, updated_at)
                VALUES (?, ?, ?, ?, datetime('now'))
                ON CONFLICT(mes_ano, category_id) DO UPDATE SET
                    amount = excluded.amount,
                    source = excluded.source,
                    updated_at = datetime('now')
            """,
                (mes_ano, cat_id, amount, source),
            )

    # ------------------------------------------------------------------ #
    # Posições da Carteira (desacoplamento total do Google Sheets & Multi-tenancy)
    # ------------------------------------------------------------------ #
    def persist_positions(self, positions: List[Position]) -> int:
        """Persiste ou atualiza posições da carteira no SQLite com isolamento multi-tenancy."""
        count = 0
        with self._conn() as c:
            for p in positions:
                c.execute(
                    "DELETE FROM positions WHERE UPPER(ticker) = UPPER(?) AND (user_id = ? OR (user_id IS NULL AND ? = 1))",
                    (p.ticker.strip(), self.user_id, self.user_id)
                )
                cur = c.execute(
                    """
                    INSERT INTO positions (
                        ticker, empresa, setor, qtd_antes, qtd_aporte,
                        preco_atual, div_proj, preco_teto, margem_seguranca,
                        feedback_usuario, insight_atual, teto_manual, custo_medio, user_id, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                    (
                        p.ticker.strip().upper(),
                        p.empresa,
                        p.setor,
                        p.qtd_antes,
                        p.qtd_aporte,
                        p.preco_atual,
                        p.div_proj,
                        p.preco_teto,
                        p.margem_seguranca,
                        p.feedback_usuario,
                        p.insight_atual,
                        1 if getattr(p, "teto_manual", False) else 0,
                        float(getattr(p, "custo_medio", 0.0) or 0.0),
                        self.user_id,
                    ),
                )
                count += cur.rowcount
        return count

    def save_positions(self, positions: List[Position]) -> int:
        """Alias para persist_positions para compatibilidade de interface."""
        return self.persist_positions(positions)

    def delete_position(self, ticker: str) -> bool:
        """Exclui uma posição da carteira de forma definitiva no SQLite para o usuário atual."""
        with self._conn() as c:
            cur = c.execute(
                "DELETE FROM positions WHERE UPPER(ticker) = UPPER(?) AND (user_id = ? OR (user_id IS NULL AND ? = 1))",
                (ticker.strip(), self.user_id, self.user_id)
            )
            c.execute("DELETE FROM sheets_positions_cache WHERE UPPER(ticker) = UPPER(?)", (ticker.strip(),))
            return cur.rowcount > 0

    def load_positions(self) -> List[Position]:
        """Carrega posições salvas no SQLite para restauração sem Google Sheets do usuário atual."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM positions WHERE (user_id = ? OR (user_id IS NULL AND ? = 1)) ORDER BY margem_seguranca DESC",
                (self.user_id, self.user_id)
            ).fetchall()
            positions = []
            for idx, r in enumerate(rows, start=10):
                teto_man = bool(r["teto_manual"]) if "teto_manual" in r.keys() else False
                c_medio = float(r["custo_medio"] or 0.0) if "custo_medio" in r.keys() else 0.0
                q_antes = r["qtd_antes"] or 0
                q_aporte = r["qtd_aporte"] or 0
                p_atual = float(r["preco_atual"] or 0.0)
                positions.append(
                    Position(
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
                        custo_medio=c_medio,
                    )
                )
            return positions

    def reconcile_ofx_to_positions(self, resumos: Dict[str, Any], target_month: Optional[str] = None) -> int:
        """Concilia transações de aporte do OFX com as posições da carteira.

        Estratégia FITID-first:
        1. Persiste todas as transações dos resumos com `persist_transacoes` (INSERT OR IGNORE por FITID).
        2. Seleciona apenas transações pendentes (`categoria = 'Aporte Ações/FIIs' AND conciliado = 0`).
        3. Se `qtd_aporte > 0`: liquida a ordem em aberto (`qtd_antes += qtd_aporte`, `qtd_aporte = 0`).
        4. Se `qtd_aporte == 0` (compra direta):
           - Se todas as transações forem anteriores ao mês alvo `target_month` (ou mês mais recente
             do resumo), apenas marca os FITIDs como conciliados sem alterar `qtd_antes`.
           - Se for do mês alvo, estima `qtd_est = int(total_liquido // preco_atual)` e adiciona.
        5. Recalcula custo médio ponderado com valor líquido real (deduzindo refunds/CREDIT).
        6. Marca cada FITID processado individualmente como `conciliado = 1`.
        """
        target_month = validate_target_month(target_month)
        positions_list = self.load_positions()
        if not positions_list:
            return 0

        # 1. Garante que todas as transações dos resumos sejam persistidas no SQLite.
        todas_transacoes = []
        for mes, resumo in resumos.items():
            txs = getattr(resumo, "transacoes", None)
            if txs:
                todas_transacoes.extend(txs)
            else:
                # Suporte a resumos sintéticos ou mocks que fornecem apenas detalhes_ativos
                detalhes = getattr(resumo, "detalhes_ativos", None)
                if detalhes is None and isinstance(resumo, dict):
                    detalhes = resumo.get("detalhes_ativos", {})
                if isinstance(detalhes, dict):
                    for tk_synth, val_synth in detalhes.items():
                        v_flt = float(val_synth or 0.0)
                        if v_flt > 0:
                            todas_transacoes.append(
                                {
                                    "id_transacao": f"SYNTH_{tk_synth.upper()}_{mes}",
                                    "data": f"{mes}-01",
                                    "tipo": "DEBIT",
                                    "valor": -abs(v_flt),
                                    "descricao": f"Aporte {tk_synth.upper()}",
                                    "categoria": "Aporte Ações/FIIs",
                                    "ticker_relacionado": tk_synth.upper(),
                                    "origem": "OFX_SYNTH",
                                }
                            )

        if todas_transacoes:
            self.persist_transacoes(todas_transacoes)

        # 2. Busca todas as transações de aporte PENDENTES (conciliado = 0)
        with self._conn() as c:
            pendentes = c.execute(
                """
                SELECT fitid, ticker, mes_ano, tipo, valor
                  FROM transacoes
                 WHERE categoria = 'Aporte Ações/FIIs'
                   AND conciliado = 0
                   AND ticker IS NOT NULL
                   AND ticker != ''
                """
            ).fetchall()

        if not pendentes:
            return 0

        # 3. Agrupa transações pendentes por ticker
        txs_por_ticker: Dict[str, List[Dict[str, Any]]] = {}
        for row in pendentes:
            tk = row["ticker"].upper().strip()
            txs_por_ticker.setdefault(tk, []).append(
                {
                    "fitid": row["fitid"],
                    "mes_ano": row["mes_ano"],
                    "tipo": row["tipo"],
                    "valor": float(row["valor"]),
                }
            )

        positions = {p.ticker.upper(): p for p in positions_list}
        updated_tickers = set()

        from datetime import datetime

        mes_atual = datetime.now().strftime("%Y-%m")
        meses_resumo = [k for k in resumos.keys() if k]
        # target_month explícito tem prioridade; senão, adota o mês mais recente do lote ou corrente
        mes_alvo = target_month or (max([mes_atual] + meses_resumo) if meses_resumo else mes_atual)

        fitids_to_mark_conciliated = []

        for tk, transacoes in txs_por_ticker.items():
            if tk not in positions:
                continue

            p = positions[tk]
            # Valor líquido real: compras (DEBIT) somam; estornos/ajustes (CREDIT) abatem
            total_valor = sum(
                abs(t["valor"]) if (t["tipo"] == "DEBIT" or t["valor"] < 0) else -abs(t["valor"]) for t in transacoes
            )
            total_valor = round(total_valor, 2)
            if total_valor <= 0:
                fitids_to_mark_conciliated.extend(t["fitid"] for t in transacoes)
                continue

            qtd_adicionada = 0
            if p.qtd_aporte > 0:
                # 1. Ordem programada liquidada
                qtd_adicionada = p.qtd_aporte
                p.qtd_antes += p.qtd_aporte
                p.qtd_aporte = 0
            else:
                # 2. Compra direta: se todas as transações forem de meses anteriores a mes_alvo,
                # são aportes históricos passados que já estão consolidados em qtd_antes!
                if all(t["mes_ano"] < mes_alvo for t in transacoes):
                    fitids_to_mark_conciliated.extend(t["fitid"] for t in transacoes)
                    continue

                if p.preco_atual > 0:
                    qtd_est = int(total_valor // p.preco_atual)
                    if qtd_est > 0:
                        p.qtd_antes += qtd_est
                        qtd_adicionada = qtd_est

            # Atualização de Custo Médio Ponderado:
            if qtd_adicionada > 0 and total_valor > 0 and p.qtd_antes > 0:
                qtd_anterior = p.qtd_antes - qtd_adicionada
                if qtd_anterior > 0:
                    if p.custo_medio and p.custo_medio > 0:
                        custo_antigo = p.custo_medio
                    else:
                        custo_antigo = p.preco_atual
                        logger.warning(
                            "reconcile_custo_medio_zero_fallback",
                            ticker=tk,
                            qtd_anterior=qtd_anterior,
                            preco_proxy=p.preco_atual,
                        )
                    novo_custo = (qtd_anterior * custo_antigo + total_valor) / p.qtd_antes
                    p.custo_medio = round(novo_custo, 2)
                else:
                    p.custo_medio = round(total_valor / qtd_adicionada, 2)

            p.qtd_total = p.qtd_antes + p.qtd_aporte
            p.valor_total = round(p.qtd_total * p.preco_atual, 2)
            p.total_aporte = round(p.qtd_aporte * p.preco_atual, 2)
            updated_tickers.add(tk)
            fitids_to_mark_conciliated.extend(t["fitid"] for t in transacoes)

        # 4. Marca cada FITID individualmente como conciliado no SQLite
        if fitids_to_mark_conciliated:
            with self._conn() as c:
                for fid in fitids_to_mark_conciliated:
                    c.execute(
                        "UPDATE transacoes SET conciliado = 1 WHERE fitid = ?",
                        (fid,),
                    )

        if updated_tickers:
            self.save_positions(list(positions.values()))
            logger.info("ofx_positions_reconciled", count=len(updated_tickers), tickers=list(updated_tickers))

        return len(updated_tickers)

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
            c.execute(
                """
                INSERT INTO notification_preferences (alert_type, enabled, updated_at, updated_by)
                VALUES (?, ?, datetime('now'), ?)
                ON CONFLICT(alert_type) DO UPDATE SET
                    enabled    = excluded.enabled,
                    updated_at = datetime('now'),
                    updated_by = excluded.updated_by
            """,
                (alert_type, 1 if enabled else 0, updated_by),
            )

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
            c.execute(
                """
                UPDATE scheduler_config
                   SET enabled      = COALESCE(?, enabled),
                       days_of_week = COALESCE(?, days_of_week),
                       hours        = COALESCE(?, hours),
                       minute       = COALESCE(?, minute),
                       updated_at   = datetime('now'),
                       updated_by   = COALESCE(?, updated_by)
                 WHERE id = 1
            """,
                (
                    None if enabled is None else (1 if enabled else 0),
                    days_of_week,
                    hours,
                    minute,
                    updated_by,
                ),
            )
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
        setores_json: Optional[str] = None,
    ) -> None:
        """Salva ou atualiza o snapshot de patrimônio para uma data (YYYY-MM-DD)."""
        with self._conn() as c:
            c.execute(
                """
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
            """,
                (
                    data_snapshot,
                    patrimonio_total,
                    cotas_totais,
                    dividendos_mes,
                    aporte_planejado,
                    rentabilidade_mes,
                    setores_json or "{}",
                ),
            )

    def get_portfolio_snapshots(self, limit_months: int = 12) -> List[Dict[str, Any]]:
        """Retorna histórico de snapshots ordenados por data."""
        with self._conn() as c:
            rows = c.execute(
                """
                SELECT data_snapshot, patrimonio_total, cotas_totais, dividendos_mes, aporte_planejado, rentabilidade_mes, setores_json
                FROM portfolio_snapshots
                ORDER BY data_snapshot ASC
                LIMIT ?
            """,
                (limit_months,),
            ).fetchall()
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
        self, current_patrimonio: float, current_cotas: int, setores_dict: Optional[Dict[str, float]] = None
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

                c.execute(
                    """
                    INSERT INTO portfolio_snapshots 
                    (data_snapshot, patrimonio_total, cotas_totais, dividendos_mes, aporte_planejado, rentabilidade_mes, setores_json)
                    VALUES (?, ?, ?, ?, ?, 0.0, ?)
                    ON CONFLICT(data_snapshot) DO UPDATE SET
                        patrimonio_total = excluded.patrimonio_total,
                        cotas_totais = excluded.cotas_totais,
                        setores_json = COALESCE(excluded.setores_json, portfolio_snapshots.setores_json)
                """,
                    (dt_str, pat, cotas, div, ap, sec_json),
                )

    def update_insight_feedback(self, ticker: str, feedback: str) -> None:
        """Atualiza o feedback do usuário na tabela positions e no último registro de historico_analises."""
        with self._conn() as c:
            c.execute(
                "UPDATE positions SET feedback_usuario = ?, updated_at = datetime('now') WHERE ticker = ?",
                (feedback, ticker),
            )
            try:
                c.execute(
                    """
                    UPDATE historico_analises 
                    SET feedback_usuario = ? 
                    WHERE id = (
                        SELECT id FROM historico_analises WHERE ticker = ? ORDER BY id DESC LIMIT 1
                    )
                """,
                    (feedback, ticker),
                )
            except sqlite3.OperationalError:
                pass

    def save_price_history(self, ticker: str, data_pregao: str, preco_fechamento: float, volume: float = 0.0) -> None:
        """Salva a cotação histórica de fechamento de um pregão."""
        with self._conn() as c:
            c.execute(
                """
                INSERT INTO price_history (ticker, data_pregao, preco_fechamento, volume)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(ticker, data_pregao) DO UPDATE SET
                    preco_fechamento = excluded.preco_fechamento,
                    volume = excluded.volume
            """,
                (ticker, data_pregao, preco_fechamento, volume),
            )

    def get_price_history(self, ticker: str, limit_days: int = 90) -> List[Dict[str, Any]]:
        """Retorna as cotações históricas dos últimos N pregões."""
        with self._conn() as c:
            rows = c.execute(
                """
                SELECT ticker, data_pregao, preco_fechamento, volume
                FROM price_history
                WHERE ticker = ?
                ORDER BY data_pregao ASC
                LIMIT ?
            """,
                (ticker, limit_days),
            ).fetchall()
            return [dict(r) for r in rows]

    def sync_yfinance_history(self, tickers: Optional[List[str]] = None) -> int:
        """Baixa o histórico diário real via yfinance e persiste no SQLite."""
        if not tickers:
            positions = self.load_positions()
            tickers = (
                [p.ticker for p in positions]
                if positions
                else ["BBAS3", "SAPR4", "BBSE3", "MXRF11", "PETR4", "KNCR11", "ITUB4", "HGCR11", "XPLG11", "CMIG4"]
            )

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
                        c.execute(
                            """
                            INSERT INTO price_history (ticker, data_pregao, preco_fechamento, volume)
                            VALUES (?, ?, ?, ?)
                            ON CONFLICT(ticker, data_pregao) DO UPDATE SET
                                preco_fechamento = excluded.preco_fechamento,
                                volume = excluded.volume
                        """,
                            (clean_ticker, dt_str, close_price, vol),
                        )
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
            dates = c.execute(
                """
                SELECT DISTINCT strftime('%Y-%m', data_pregao) as ym, MAX(data_pregao) as end_date
                FROM price_history
                GROUP BY ym
                ORDER BY end_date ASC
                LIMIT ?
            """,
                (limit_months,),
            ).fetchall()

            if not dates or len(dates) < 3:
                if soma_pat > 0 and len(positions) > 0:
                    self.seed_initial_snapshots_if_empty(round(soma_pat, 2), soma_cotas, setores_base)
                    return self.get_portfolio_snapshots(limit_months)
                return []

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
                        (p.ticker, dt_str),
                    ).fetchone()
                    preco = float(row_p["preco_fechamento"]) if row_p else p.preco_atual
                    val = (p.qtd_antes or 0) * preco
                    soma_pat_m += val
                    soma_cotas_m += p.qtd_antes or 0
                    sec = p.setor or "B.E.S.T."
                    month_setores[sec] = round(month_setores.get(sec, 0.0) + val, 2)

                if soma_pat_m > 0:
                    c.execute(
                        """
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
                    """,
                        (
                            dt_str,
                            round(soma_pat_m, 2),
                            soma_cotas_m,
                            round(soma_pat_m * 0.009, 2),
                            1000.0,
                            0.0,
                            json.dumps(month_setores, ensure_ascii=False),
                        ),
                    )

            rows = c.execute(
                """
                SELECT * FROM portfolio_snapshots 
                ORDER BY data_snapshot ASC 
                LIMIT ?
            """,
                (limit_months,),
            ).fetchall()

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
            cur = c.execute(
                """
                INSERT INTO pending_sheets_writes (ticker, payload_json)
                VALUES (?, ?)
            """,
                (ticker, payload_str),
            )
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
                    "insight_atual": p.insight_atual,
                }
                c.execute(
                    """
                    INSERT INTO sheets_positions_cache (ticker, payload_json, expires_at, updated_at)
                    VALUES (?, ?, ?, datetime('now'))
                    ON CONFLICT(ticker) DO UPDATE SET
                        payload_json = excluded.payload_json,
                        expires_at = excluded.expires_at,
                        updated_at = datetime('now')
                """,
                    (p.ticker, json.dumps(data, ensure_ascii=False), expires_at),
                )

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
                    positions.append(
                        Position(
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
                            insight_atual=d.get("insight_atual", ""),
                        )
                    )
                except Exception:
                    pass
            return (positions if positions else None), is_stale

    # ------------------------------------------------------------------ #
    # Rastreamento de Dividendos Reais (P0: dividends_received)
    # ------------------------------------------------------------------ #
    def record_dividend_received(
        self,
        ticker: Any,
        data_pagamento: Optional[str] = None,
        valor_total: Optional[float] = None,
        valor_por_cota: float = 0.0,
        quantidade: int = 0,
        tipo: str = "DIVIDENDO",
        fonte: str = "manual",
    ) -> int:
        """Registra um dividendo real recebido no SQLite. Aceita argumentos posicionais ou instância de DividendReceived."""
        if hasattr(ticker, "ticker") and hasattr(ticker, "valor_total"):
            obj = ticker
            ticker = getattr(obj, "ticker", "")
            data_pagamento = getattr(obj, "data_pagamento", None) or getattr(obj, "mes_ano", "")
            valor_total = float(getattr(obj, "valor_total", 0.0))
            valor_por_cota = float(getattr(obj, "valor_por_cota", 0.0) or 0.0)
            quantidade = int(getattr(obj, "quantidade", 0) or 0)
            tipo = str(getattr(obj, "tipo", "DIVIDENDO"))
            fonte = str(getattr(obj, "fonte", "manual"))

        ticker = str(ticker).strip().upper()
        dp = str(data_pagamento or "")
        mes_ano = dp[:7] if len(dp) >= 7 else datetime.now().strftime("%Y-%m")
        val = float(valor_total if valor_total is not None else 0.0)

        with self._conn() as c:
            cur = c.execute(
                """
                INSERT INTO dividends_received (
                    ticker, data_pagamento, mes_ano, valor_total, valor_por_cota,
                    quantidade, tipo, fonte, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """,
                (ticker, dp, mes_ano, val, valor_por_cota, quantidade, tipo, fonte),
            )
            return cur.lastrowid

    def list_dividends_received(
        self, ticker: Optional[str] = None, mes_ano: Optional[str] = None, year: Optional[int] = None, limit: int = 100
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
            rows = c.execute(
                """
                SELECT mes_ano, SUM(valor_total) as total_mes, COUNT(*) as qtd_lancamentos
                FROM dividends_received
                GROUP BY mes_ano
                ORDER BY mes_ano DESC
                LIMIT ?
            """,
                (months,),
            ).fetchall()
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
        search: Optional[str] = None,
        limit: int = 50,
        offset: int = 0
    ) -> List[Dict[str, Any]]:
        """Retorna transações bancárias/cartão persistidas com filtros e busca."""
        query = "SELECT * FROM transacoes WHERE 1=1"
        params: List[Any] = []
        if mes_ano:
            query += " AND mes_ano = ?"
            params.append(mes_ano)
        if categoria:
            query += " AND categoria = ?"
            params.append(categoria)
        if search:
            query += " AND (descricao LIKE ? OR categoria LIKE ?)"
            term = f"%{search.strip()}%"
            params.extend([term, term])
        query += " ORDER BY data DESC, id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self._conn() as c:
            rows = c.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def count_transacoes(
        self,
        mes_ano: Optional[str] = None,
        categoria: Optional[str] = None,
        search: Optional[str] = None
    ) -> int:
        """Retorna a contagem total de transações de acordo com os filtros."""
        query = "SELECT COUNT(*) FROM transacoes WHERE 1=1"
        params: List[Any] = []
        if mes_ano:
            query += " AND mes_ano = ?"
            params.append(mes_ano)
        if categoria:
            query += " AND categoria = ?"
            params.append(categoria)
        if search:
            query += " AND (descricao LIKE ? OR categoria LIKE ?)"
            term = f"%{search.strip()}%"
            params.extend([term, term])

        with self._conn() as c:
            row = c.execute(query, params).fetchone()
            return row[0] if row else 0

    def delete_transacao(self, transacao_id: int) -> bool:
        """Exclui uma transação bancária persistida por ID."""
        with self._conn() as c:
            cur = c.execute("DELETE FROM transacoes WHERE id = ?", (transacao_id,))
            return cur.rowcount > 0

    def update_transacao(
        self,
        transacao_id: int,
        descricao: str,
        categoria: str,
        valor: float,
        data: str,
        tipo: Optional[str] = None
    ) -> bool:
        """Atualiza campos de uma transação existente."""
        mes_ano = data[:7] if len(data) >= 7 else None
        with self._conn() as c:
            if tipo:
                cur = c.execute(
                    """
                    UPDATE transacoes
                       SET descricao = ?, categoria = ?, valor = ?, data = ?, tipo = ?, mes_ano = COALESCE(?, mes_ano)
                     WHERE id = ?
                """,
                    (descricao.strip(), categoria.strip(), float(valor), data.strip(), tipo.strip().upper(), mes_ano, transacao_id),
                )
            else:
                cur = c.execute(
                    """
                    UPDATE transacoes
                       SET descricao = ?, categoria = ?, valor = ?, data = ?, mes_ano = COALESCE(?, mes_ano)
                     WHERE id = ?
                """,
                    (descricao.strip(), categoria.strip(), float(valor), data.strip(), mes_ano, transacao_id),
                )
            return cur.rowcount > 0

    def get_budget_overview(self, mes_ano: Optional[str] = None) -> Dict[str, Any]:
        """Calcula a visão consolidada de Orçamento (Previsto vs Realizado) do mês."""
        if not mes_ano:
            mes_ano = datetime.now().strftime("%Y-%m")

        recurring = self.get_all_expenses(mes_ano)
        with self._conn() as c:
            # Gastos reais por categoria no mês via tabela transacoes
            rows = c.execute(
                """
                SELECT categoria,
                       SUM(CASE WHEN tipo = 'DEBIT' THEN valor ELSE 0 END) as total_debito,
                       SUM(CASE WHEN tipo = 'CREDIT' THEN valor ELSE 0 END) as total_credito
                FROM transacoes
                WHERE mes_ano = ?
                GROUP BY categoria
            """,
                (mes_ano,),
            ).fetchall()
            realizado_map = {r["categoria"]: float(r["total_debito"] or 0.0) for r in rows}
            creditos_map = {r["categoria"]: float(r["total_credito"] or 0.0) for r in rows}

        total_entradas_real = sum(
            c
            for cat, c in creditos_map.items()
            if "SALÁRIO" in cat.upper() or "RECEITA" in cat.upper() or "ENTRADA" in cat.upper()
        )
        total_saidas_real = sum(realizado_map.values())

        # Consolida itens comparativos
        all_cats = sorted(set(list(recurring.keys()) + list(realizado_map.keys())))
        items = []
        for cat in all_cats:
            previsto = recurring.get(cat, 0.0)
            realizado = realizado_map.get(cat, 0.0)
            delta = round(realizado - previsto, 2)
            items.append(
                {
                    "categoria": cat,
                    "previsto": previsto,
                    "realizado": realizado,
                    "delta": delta,
                    "status": "CONFORME" if abs(delta) < 1.0 else ("ECONOMIA" if delta < 0 else "ATENÇÃO"),
                }
            )

        salario_previsto = self.get_expense_amount(mes_ano, "Salário / Renda Principal")
        total_entradas = total_entradas_real if total_entradas_real > 0 else salario_previsto

        return {
            "mes_ano": mes_ano,
            "total_entradas": round(total_entradas, 2),
            "total_saidas": round(total_saidas_real, 2),
            "saldo_livre": round(total_entradas - total_saidas_real, 2),
            "items": items,
        }

    # -------------------------------------------------------------
    # GESTÃO DE ARTEFATOS PERSONALIZADOS (RELATÓRIOS DINÂMICOS)
    # -------------------------------------------------------------
    def get_available_data_sources(self) -> Dict[str, Any]:
        """Retorna fontes de dados e metadados de campos permitidos para construção de artefatos."""
        return {
            "positions": {
                "name": "Posições da Carteira",
                "fields": [
                    {"id": "ticker", "name": "Ticker (ex: BBAS3)", "type": "string"},
                    {"id": "empresa", "name": "Empresa", "type": "string"},
                    {"id": "setor", "name": "Setor (B.E.S.T.)", "type": "string"},
                    {"id": "preco_atual", "name": "Preço Atual (R$)", "type": "number"},
                    {"id": "preco_teto", "name": "Preço Teto Barsi (R$)", "type": "number"},
                    {"id": "margem_seguranca", "name": "Margem de Segurança (%)", "type": "number"},
                    {"id": "div_proj", "name": "Dividendo Projetado (R$)", "type": "number"},
                    {"id": "qtd_antes", "name": "Quantidade de Ações", "type": "number"},
                    {"id": "custo_medio", "name": "Custo Médio (R$)", "type": "number"},
                    {"id": "valor_mercado", "name": "Valor de Mercado (R$)", "type": "number"},
                ],
            },
            "portfolio_snapshots": {
                "name": "Evolução Histórica do Patrimônio",
                "fields": [
                    {"id": "data_snapshot", "name": "Data do Snapshot", "type": "string"},
                    {"id": "patrimonio_total", "name": "Patrimônio Total (R$)", "type": "number"},
                    {"id": "cotas_totais", "name": "Total de Cotas/Ações", "type": "number"},
                    {"id": "dividendos_mes", "name": "Dividendos no Mês (R$)", "type": "number"},
                    {"id": "aporte_planejado", "name": "Aporte Planejado (R$)", "type": "number"},
                    {"id": "rentabilidade_mes", "name": "Rentabilidade Mês (%)", "type": "number"},
                ],
            },
            "dividends_received": {
                "name": "Dividendos Recebidos",
                "fields": [
                    {"id": "ticker", "name": "Ticker", "type": "string"},
                    {"id": "mes_ano", "name": "Mês/Ano (YYYY-MM)", "type": "string"},
                    {"id": "data_pagamento", "name": "Data Pagamento", "type": "string"},
                    {"id": "valor_total", "name": "Valor Total Recebido (R$)", "type": "number"},
                    {"id": "valor_por_cota", "name": "Valor por Cota (R$)", "type": "number"},
                    {"id": "quantidade", "name": "Quantidade de Cotas", "type": "number"},
                    {"id": "tipo", "name": "Tipo (Dividendo/JCP)", "type": "string"},
                ],
            },
            "transacoes": {
                "name": "Transações / Fluxo Financeiro",
                "fields": [
                    {"id": "mes_ano", "name": "Mês/Ano", "type": "string"},
                    {"id": "categoria", "name": "Categoria", "type": "string"},
                    {"id": "data", "name": "Data", "type": "string"},
                    {"id": "tipo", "name": "Tipo (CREDIT/DEBIT)", "type": "string"},
                    {"id": "origem", "name": "Origem (CONTA/CARTAO)", "type": "string"},
                    {"id": "valor", "name": "Valor (R$)", "type": "number"},
                ],
            },
        }

    def create_custom_artifact(
        self,
        name: str,
        chart_type: str,
        data_source: str,
        fields: List[str],
        description: Optional[str] = None,
        filters: Optional[Dict[str, Any]] = None,
        visible_in: Optional[List[str]] = None,
    ) -> int:
        """Cria uma nova definição de artefato dinâmico com validação estrita de fontes e colunas."""
        name = (name or "").strip()
        if not name:
            raise ValueError("Nome do artefato é obrigatório")

        chart_type = (chart_type or "").lower().strip()
        if chart_type not in VALID_CHART_TYPES:
            raise ValueError(f"Tipo de gráfico inválido: {chart_type}. Permitidos: {sorted(list(VALID_CHART_TYPES))}")

        data_source = (data_source or "").lower().strip()
        if data_source not in VALID_DATA_SOURCES:
            raise ValueError(
                f"Fonte de dados inválida: {data_source}. Permitidas: {sorted(list(VALID_DATA_SOURCES.keys()))}"
            )

        if not fields or not isinstance(fields, list):
            raise ValueError("Lista de campos não pode ser vazia")

        allowed = VALID_DATA_SOURCES[data_source]["allowed_fields"]
        invalid_fields = [f for f in fields if f not in allowed]
        if invalid_fields:
            raise ValueError(
                f"Campos inválidos para '{data_source}': {invalid_fields}. Permitidos: {list(allowed.keys())}"
            )

        visible = (
            visible_in
            if (visible_in is not None and isinstance(visible_in, list) and len(visible_in) > 0)
            else ["analytics"]
        )
        fields_json = json.dumps(fields)
        filters_json = json.dumps(filters or {})
        visible_json = json.dumps(visible)

        with self._conn() as conn:
            cursor = conn.execute(
                """INSERT INTO custom_artifacts 
                   (name, description, chart_type, data_source, fields, filters, visible_in, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                (name, description or "", chart_type, data_source, fields_json, filters_json, visible_json),
            )
            return cursor.lastrowid

    def list_custom_artifacts(self, visible_in: Optional[str] = None) -> List[Dict[str, Any]]:
        """Lista os artefatos cadastrados, com filtro opcional por página de exibição."""
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM custom_artifacts ORDER BY id ASC").fetchall()
            result = []
            for r in rows:
                try:
                    vis = json.loads(r["visible_in"]) if r["visible_in"] else ["analytics"]
                except Exception:
                    vis = ["analytics"]
                if visible_in and visible_in not in vis:
                    continue
                try:
                    flds = json.loads(r["fields"]) if r["fields"] else []
                except Exception:
                    flds = []
                try:
                    flts = json.loads(r["filters"]) if r["filters"] else {}
                except Exception:
                    flts = {}
                result.append(
                    {
                        "id": r["id"],
                        "name": r["name"],
                        "description": r["description"] or "",
                        "chart_type": r["chart_type"],
                        "data_source": r["data_source"],
                        "fields": flds,
                        "filters": flts,
                        "visible_in": vis,
                        "created_at": r["created_at"],
                        "updated_at": r["updated_at"],
                    }
                )
            return result

    def get_custom_artifact(self, artifact_id: int) -> Optional[Dict[str, Any]]:
        """Busca definição de um artefato pelo ID."""
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM custom_artifacts WHERE id = ?", (artifact_id,)).fetchone()
            if not row:
                return None
            try:
                vis = json.loads(row["visible_in"]) if row["visible_in"] else ["analytics"]
            except Exception:
                vis = ["analytics"]
            try:
                flds = json.loads(row["fields"]) if row["fields"] else []
            except Exception:
                flds = []
            try:
                flts = json.loads(row["filters"]) if row["filters"] else {}
            except Exception:
                flts = {}
            return {
                "id": row["id"],
                "name": row["name"],
                "description": row["description"] or "",
                "chart_type": row["chart_type"],
                "data_source": row["data_source"],
                "fields": flds,
                "filters": flts,
                "visible_in": vis,
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }

    def delete_custom_artifact(self, artifact_id: int) -> bool:
        """Exclui um artefato pelo ID."""
        with self._conn() as conn:
            cursor = conn.execute("DELETE FROM custom_artifacts WHERE id = ?", (artifact_id,))
            return cursor.rowcount > 0

    def get_custom_artifact_data(self, artifact_id: int) -> Dict[str, Any]:
        """Executa a consulta SQL parametrizada de forma segura e prepara dados para Chart.js e Excel."""
        art = self.get_custom_artifact(artifact_id)
        if not art:
            raise ValueError(f"Artefato {artifact_id} não encontrado")

        source_cfg = VALID_DATA_SOURCES.get(art["data_source"])
        if not source_cfg:
            raise ValueError(f"Fonte '{art['data_source']}' inválida")

        table = source_cfg["table"]
        allowed = source_cfg["allowed_fields"]
        chart_type = art["chart_type"]
        fields = art["fields"]

        label_field = fields[0]
        metric_fields = fields[1:] if len(fields) > 1 else []

        # Determina agrupamento se gráfico circular ou se label for categórico com métrica numérica
        should_group = chart_type in ("doughnut", "pie") or (
            label_field in ("setor", "categoria", "tipo", "origem", "mes_ano") and bool(metric_fields)
        )

        where_parts = []
        params = []
        filters = art.get("filters") or {}
        for k, v in filters.items():
            if k in allowed and v is not None and str(v).strip() != "":
                where_parts.append(f"{k} = ?")
                params.append(v)

        if should_group:
            select_parts = [f"{allowed[label_field]} AS {label_field}"]
            for m in metric_fields:
                expr = allowed[m]
                select_parts.append(f"SUM({expr}) AS {m}")
            sql = f"SELECT {', '.join(select_parts)} FROM {table}"
            if where_parts:
                sql += " WHERE " + " AND ".join(where_parts)
            sql += f" GROUP BY {allowed[label_field]}"
            if metric_fields:
                sql += f" ORDER BY {metric_fields[0]} DESC"
        else:
            select_parts = []
            for f in fields:
                expr = allowed[f]
                select_parts.append(f"{expr} AS {f}" if expr != f else f)
            sql = f"SELECT {', '.join(select_parts)} FROM {table}"
            if where_parts:
                sql += " WHERE " + " AND ".join(where_parts)
            order = source_cfg.get("default_order")
            if order:
                sql += f" ORDER BY {order}"

        sql += " LIMIT 150"

        with self._conn() as conn:
            raw_rows = [dict(r) for r in conn.execute(sql, params).fetchall()]

        labels: List[str] = []
        values: List[float] = []
        datasets: List[Dict[str, Any]] = []

        if raw_rows:
            labels = [str(r.get(label_field, "")) for r in raw_rows]
            active_metrics = metric_fields if metric_fields else [label_field]
            for m in active_metrics:
                m_vals = []
                for r in raw_rows:
                    val = r.get(m)
                    try:
                        m_vals.append(round(float(val), 2) if val is not None else 0.0)
                    except (ValueError, TypeError):
                        m_vals.append(0.0)
                datasets.append(
                    {
                        "label": m.replace("_", " ").title(),
                        "data": m_vals,
                        "field": m,
                    }
                )
                if not values:
                    values = m_vals

        return {
            "id": art["id"],
            "name": art["name"],
            "description": art["description"],
            "chart_type": art["chart_type"],
            "data_source": art["data_source"],
            "fields": fields,
            "filters": art["filters"],
            "visible_in": art["visible_in"],
            "labels": labels,
            "values": values,
            "datasets": datasets,
            "rows": raw_rows,
        }

    # ---------------------------------------------------------
    # GESTÃO DE METAS FINANCEIRAS (ROADMAP V3.0)
    # ---------------------------------------------------------
    def seed_default_goals(self) -> None:
        """Inicializa metas padrão se a tabela estiver vazia."""
        with self._conn() as conn:
            count = conn.execute("SELECT COUNT(*) as c FROM financial_goals").fetchone()["c"]
            if count == 0:
                for g in DEFAULT_GOAL_SEEDS:
                    conn.execute(
                        """
                        INSERT INTO financial_goals (tipo, titulo, ticker, valor_alvo, prazo_meses, notas)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (g["tipo"], g["titulo"], g.get("ticker"), g["valor_alvo"], g.get("prazo_meses"), g.get("notas"))
                    )

    def list_goals(self) -> List[Dict[str, Any]]:
        """Lista todas as metas financeiras cadastradas para o usuário atual."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM financial_goals WHERE (user_id = ? OR (user_id IS NULL AND ? = 1)) ORDER BY id ASC",
                (self.user_id, self.user_id)
            ).fetchall()
            return [dict(r) for r in rows]

    def create_goal(
        self,
        tipo: str,
        titulo: str,
        valor_alvo: float,
        ticker: Optional[str] = None,
        prazo_meses: Optional[int] = None,
        notas: Optional[str] = None
    ) -> int:
        """Cria uma nova meta financeira vinculada ao usuário atual."""
        if tipo not in ("renda_passiva", "patrimonio", "acoes_ticker", "aporte_mensal", "custom"):
            raise ValueError(f"Tipo de meta inválido: {tipo}")
        if valor_alvo <= 0:
            raise ValueError("O valor alvo deve ser maior que zero.")

        ticker_clean = ticker.strip().upper() if ticker else None

        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO financial_goals (tipo, titulo, ticker, valor_alvo, prazo_meses, notas, user_id)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (tipo, titulo.strip(), ticker_clean, float(valor_alvo), prazo_meses, notas, self.user_id)
            )
            return cur.lastrowid

    def update_goal(
        self,
        goal_id: int,
        tipo: str,
        titulo: str,
        valor_alvo: float,
        ticker: Optional[str] = None,
        prazo_meses: Optional[int] = None,
        notas: Optional[str] = None
    ) -> bool:
        """Atualiza uma meta existente do usuário atual."""
        if tipo not in ("renda_passiva", "patrimonio", "acoes_ticker", "aporte_mensal", "custom"):
            raise ValueError(f"Tipo de meta inválido: {tipo}")
        if valor_alvo <= 0:
            raise ValueError("O valor alvo deve ser maior que zero.")

        ticker_clean = ticker.strip().upper() if ticker else None

        with self._conn() as conn:
            cur = conn.execute(
                """
                UPDATE financial_goals
                SET tipo = ?, titulo = ?, ticker = ?, valor_alvo = ?, prazo_meses = ?, notas = ?, updated_at = datetime('now')
                WHERE id = ? AND (user_id = ? OR (user_id IS NULL AND ? = 1))
                """,
                (tipo, titulo.strip(), ticker_clean, float(valor_alvo), prazo_meses, notas, goal_id, self.user_id, self.user_id)
            )
            return cur.rowcount > 0

    def delete_goal(self, goal_id: int) -> bool:
        """Exclui uma meta existente do usuário atual."""
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM financial_goals WHERE id = ? AND (user_id = ? OR (user_id IS NULL AND ? = 1))",
                (goal_id, self.user_id, self.user_id)
            )
            return cur.rowcount > 0

    def get_goals_with_progress(self) -> List[Dict[str, Any]]:
        """Recupera as metas calculando o progresso atual em tempo real contra o SQLite."""
        goals = self.list_goals()
        positions = self.load_positions()

        # Cache e agregações
        total_patrimonio = sum(p.valor_total or ((p.qtd_antes + p.qtd_aporte) * p.preco_atual) for p in positions)
        proj_anual_div = sum((p.div_proj or 0.0) * (p.qtd_antes + p.qtd_aporte) for p in positions)
        proj_mensal_div = round(proj_anual_div / 12.0, 2)

        # Histórico de dividendos reais recebidos nos últimos 12 meses
        with self._conn() as conn:
            div_row = conn.execute(
                """
                SELECT SUM(valor_total) as total_div
                FROM dividends_received
                WHERE data_pagamento >= date('now', '-365 days')
                """
            ).fetchone()
            total_div_12m = float(div_row["total_div"] or 0.0)
            real_mensal_div = round(total_div_12m / 12.0, 2)

            # Aporte médio mensal realizado nos últimos 3 meses (transações de investimento)
            inv_row = conn.execute(
                """
                SELECT SUM(t.valor) as total_inv
                FROM transacoes t
                JOIN expense_categories c ON t.categoria_id = c.id
                WHERE c.kind = 'investimento' AND t.tipo = 'DEBIT' AND t.data >= date('now', '-90 days')
                """
            ).fetchone()
            total_inv_90d = float(inv_row["total_inv"] or 0.0)
            media_aporte_mensal = round(total_inv_90d / 3.0, 2) if total_inv_90d > 0 else 0.0

        positions_by_ticker = {p.ticker.upper(): p for p in positions}

        results: List[Dict[str, Any]] = []
        for g in goals:
            tipo = g["tipo"]
            alvo = float(g["valor_alvo"])
            atual = 0.0
            unidade = "R$"

            if tipo == "renda_passiva":
                atual = max(proj_mensal_div, real_mensal_div)
                unidade = "R$/mês"
            elif tipo == "patrimonio":
                atual = total_patrimonio
                unidade = "R$"
            elif tipo == "acoes_ticker":
                unidade = "ações"
                t = (g["ticker"] or "").upper()
                pos = positions_by_ticker.get(t)
                if pos:
                    atual = float(pos.qtd_antes + pos.qtd_aporte)
                else:
                    atual = 0.0
            elif tipo == "aporte_mensal":
                atual = media_aporte_mensal
                unidade = "R$/mês"
            else:
                atual = 0.0
                unidade = "pts"

            pct = round(min(100.0, (atual / alvo) * 100), 1) if alvo > 0 else 0.0
            restante = max(0.0, round(alvo - atual, 2))
            concluida = (atual >= alvo)

            # Estimativa de tempo restante (em meses)
            meses_restantes: Optional[int] = None
            if not concluida and restante > 0:
                if tipo == "patrimonio" and media_aporte_mensal > 0:
                    meses_restantes = int(restante / media_aporte_mensal)
                elif g.get("prazo_meses"):
                    meses_restantes = g["prazo_meses"]

            results.append({
                "id": g["id"],
                "tipo": tipo,
                "titulo": g["titulo"],
                "ticker": g["ticker"],
                "valor_alvo": alvo,
                "valor_atual": round(atual, 2),
                "unidade": unidade,
                "progresso_pct": pct,
                "restante": restante,
                "concluida": concluida,
                "prazo_meses": g["prazo_meses"],
                "meses_restantes": meses_restantes,
                "notas": g["notas"],
                "created_at": g["created_at"]
            })

        return results

    # ---------------------------------------------------------
    # ALERTAS DE MUDANÇA DE TESE (EARLY WARNING NLP ROADMAP V3.0)
    # ---------------------------------------------------------
    def record_thesis_alert(
        self,
        ticker: str,
        risk_type: str,
        severity: str,
        headline: str,
        snippet: str,
        source: str = "google_rss"
    ) -> int:
        """Registra um alerta crítico de quebra ou mudança de tese Barsi."""
        ticker = ticker.strip().upper()
        with self._conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO thesis_alerts (ticker, risk_type, severity, headline, snippet, source, active, created_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, datetime('now'))
                """,
                (ticker, risk_type, severity, headline.strip(), snippet.strip(), source)
            )
            return cur.lastrowid

    def list_thesis_alerts(self, ticker: Optional[str] = None, active_only: bool = True) -> List[Dict[str, Any]]:
        """Lista alertas de mudança de tese com filtros opcionais."""
        query = "SELECT * FROM thesis_alerts WHERE 1=1"
        params: List[Any] = []
        if ticker:
            query += " AND ticker = ?"
            params.append(ticker.strip().upper())
        if active_only:
            query += " AND active = 1"
        query += " ORDER BY id DESC"

        with self._conn() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def dismiss_thesis_alert(self, alert_id: int) -> bool:
        """Marca um alerta como reconhecido/arquivado pelo investidor."""
        with self._conn() as conn:
            cur = conn.execute(
                """
                UPDATE thesis_alerts
                SET active = 0, dismissed_at = datetime('now')
                WHERE id = ?
                """,
                (alert_id,)
            )
            return cur.rowcount > 0

