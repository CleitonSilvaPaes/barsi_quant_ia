"""Definições de Schema SQLite e Constantes Iniciais para o Repositório Barsi.

Centraliza DDLs, sementes padrão de orçamento, regras de categorização por palavras-chave,
definições de artefatos personalizados e preferências de alertas/agendamento.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

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

    -- 17. Definições de Artefatos Personalizados (Relatórios Dinâmicos)
    CREATE TABLE IF NOT EXISTS custom_artifacts (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        name          TEXT NOT NULL UNIQUE,
        description   TEXT,
        chart_type    TEXT NOT NULL,
        data_source   TEXT NOT NULL,
        fields        TEXT NOT NULL,
        filters       TEXT,
        visible_in    TEXT NOT NULL DEFAULT '["analytics"]',
        created_at    TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
    );

    -- 18. Metas Financeiras Barsi (Dashboard de Metas & Objetivos)
    CREATE TABLE IF NOT EXISTS financial_goals (
        id            INTEGER PRIMARY KEY AUTOINCREMENT,
        tipo          TEXT NOT NULL CHECK (tipo IN ('renda_passiva', 'patrimonio', 'acoes_ticker', 'aporte_mensal', 'custom')),
        titulo        TEXT NOT NULL,
        ticker        TEXT,
        valor_alvo    REAL NOT NULL CHECK (valor_alvo > 0),
        prazo_meses   INTEGER,
        notas         TEXT,
        created_at    TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
    );
"""

DEFAULT_GOAL_SEEDS: List[Dict[str, Any]] = [
    {
        "tipo": "renda_passiva",
        "titulo": "Primeiro Salário Mínimo em Dividendos",
        "valor_alvo": 1500.0,
        "ticker": None,
        "prazo_meses": 24,
        "notas": "Meta Barsi: Renda passiva mensal média cobrindo despesas essenciais."
    },
    {
        "tipo": "patrimonio",
        "titulo": "Patrimônio Acumulado de R$ 100.000",
        "valor_alvo": 100000.0,
        "ticker": None,
        "prazo_meses": 36,
        "notas": "Ponto de inflexão da bola de neve dos juros compostos."
    },
    {
        "tipo": "acoes_ticker",
        "titulo": "1.000 Ações de Banco do Brasil (BBAS3)",
        "valor_alvo": 1000.0,
        "ticker": "BBAS3",
        "prazo_meses": 12,
        "notas": "Foco no número de ações geradoras no setor bancário perene."
    }
]

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

# ⚠️ Valores sintéticos de demonstração para onboarding inicial e testes automatizados.
# Não contêm valores ou despesas financeiras reais (em conformidade com .rules § 2 e § 7).
# Em instalações reais, configure via interface web (/budget), extratos bancários (.ofx)
# ou crie um arquivo local gitignored 'memory/seeds_local.json' (ou defina BARSI_SEEDS_JSON).
DEFAULT_BUDGET_SEEDS: List[Tuple[str, float, str, str]] = [
    ("Reserva de Emergência (Exemplo)", 100.00, "2026-01-01", "reserva"),
    ("Moradia / Despesas Fixas (Exemplo)", 500.00, "2026-01-01", "fixa"),
    ("Compromissos Recorrentes (Exemplo)", 300.00, "2026-01-01", "fixa"),
    ("Tributos & Taxas (Exemplo)", 50.00, "2026-01-01", "tributo"),
    ("Alimentação & Mercado (Exemplo)", 400.00, "2026-01-01", "variavel"),
    ("Transporte & Combustível (Exemplo)", 150.00, "2026-01-01", "variavel"),
]

# Fatores da curva histórica sintética para seed inicial de visualização (6 meses precedentes).
# Simulam a evolução progressiva do portfólio no primeiro acesso antes da consolidação de snapshots reais.
DEFAULT_SYNTHETIC_CURVE_FACTORS = (0.45, 0.55, 0.67, 0.78, 0.90, 1.0)

DEFAULT_SCHEDULER_DAYS = "1,2,3,4,5"
DEFAULT_SCHEDULER_HOURS = "9,10,11,12,13,14,15,16,17,18"

DEFAULT_NOTIFICATION_PREFS: List[Tuple[str, int]] = [
    ("margem_20", 1),  # alertas individuais >= 20% de margem
    ("radar_summary", 1),  # resumo horário consolidado
]

VALID_ALERT_TYPES = {alert_type for alert_type, _ in DEFAULT_NOTIFICATION_PREFS}

VALID_CHART_TYPES = {"line", "bar", "doughnut", "pie"}

VALID_DATA_SOURCES: Dict[str, Any] = {
    "positions": {
        "name": "Posições da Carteira",
        "table": "positions",
        "allowed_fields": {
            "ticker": "ticker",
            "empresa": "empresa",
            "setor": "setor",
            "preco_atual": "preco_atual",
            "preco_teto": "preco_teto",
            "margem_seguranca": "margem_seguranca",
            "div_proj": "div_proj",
            "qtd_antes": "qtd_antes",
            "qtd_aporte": "qtd_aporte",
            "custo_medio": "custo_medio",
            "valor_mercado": "((COALESCE(qtd_antes, 0) + COALESCE(qtd_aporte, 0)) * COALESCE(preco_atual, 0.0))",
        },
        "default_order": "margem_seguranca DESC",
    },
    "portfolio_snapshots": {
        "name": "Evolução do Patrimônio (Snapshots)",
        "table": "portfolio_snapshots",
        "allowed_fields": {
            "data_snapshot": "data_snapshot",
            "patrimonio_total": "patrimonio_total",
            "cotas_totais": "cotas_totais",
            "dividendos_mes": "dividendos_mes",
            "aporte_planejado": "aporte_planejado",
            "rentabilidade_mes": "rentabilidade_mes",
        },
        "default_order": "data_snapshot ASC",
    },
    "dividends_received": {
        "name": "Dividendos Recebidos",
        "table": "dividends_received",
        "allowed_fields": {
            "ticker": "ticker",
            "data_pagamento": "data_pagamento",
            "mes_ano": "mes_ano",
            "valor_total": "valor_total",
            "valor_por_cota": "valor_por_cota",
            "quantidade": "quantidade",
            "tipo": "tipo",
            "fonte": "fonte",
        },
        "default_order": "data_pagamento ASC",
    },
    "dividends": {
        "name": "Dividendos Recebidos",
        "table": "dividends_received",
        "allowed_fields": {
            "ticker": "ticker",
            "data_pagamento": "data_pagamento",
            "mes_ano": "mes_ano",
            "valor_total": "valor_total",
            "valor_por_cota": "valor_por_cota",
            "quantidade": "quantidade",
            "tipo": "tipo",
            "fonte": "fonte",
        },
        "default_order": "data_pagamento ASC",
    },
    "transacoes": {
        "name": "Transações / Fluxo Financeiro",
        "table": "transacoes",
        "allowed_fields": {
            "data": "data",
            "mes_ano": "mes_ano",
            "tipo": "tipo",
            "valor": "valor",
            "descricao": "descricao",
            "categoria": "categoria",
            "origem": "origem",
        },
        "default_order": "data ASC",
    },
}
