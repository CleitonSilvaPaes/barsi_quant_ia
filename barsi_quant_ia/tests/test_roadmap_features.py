"""Testes automatizados das funcionalidades do Roadmap (P0, 2FA TOTP, Dividends & YoC)."""
import os
import pytest
from fastapi.testclient import TestClient
from datetime import datetime, timedelta

from barsi.domain.models import Position, DividendReceived
from barsi.infra.security import (
    generate_totp_secret,
    generate_totp_code,
    verify_totp_code,
    get_totp_uri,
    create_session_token,
)
from barsi.adapters.history.user_repository import UserRepository
from barsi.adapters.history.budget_repository import BudgetRepository
from barsi.api.http import app
from barsi.config import settings

client = TestClient(app)

def test_position_custo_medio_and_yield_on_cost():
    """Valida cálculo de Yield on Cost: (div_proj / custo_medio) * 100."""
    p = Position(
        ticker="BBAS3",
        row_index=10,
        preco_atual=28.0,
        div_proj=2.80,
        custo_medio=20.0
    )
    assert p.custo_medio == 20.0
    # 2.80 / 20.0 = 14%
    assert p.yield_on_cost == 14.0

    # Quando custo_medio é 0, yield_on_cost deve ser 0.0
    p2 = Position(ticker="ITUB4", row_index=11, preco_atual=35.0, div_proj=2.0, custo_medio=0.0)
    assert p2.yield_on_cost == 0.0

def test_totp_crypto_and_verification():
    """Valida geração RFC 6238 de segredo, cálculo de 6 dígitos e validação com janela de tempo."""
    import time
    secret = generate_totp_secret()
    assert len(secret) >= 26
    
    code = generate_totp_code(secret)
    assert len(code) == 6
    assert code.isdigit()
    
    # Verificação com código correto
    assert verify_totp_code(secret, code) is True
    # Verificação com código de 1 hora atrás (garantidamente fora da janela de tolerância de 30s)
    code_past = generate_totp_code(secret, timestamp=int(time.time()) - 3600)
    assert verify_totp_code(secret, code_past) is False

    uri = get_totp_uri(secret, "investidor_demo")
    assert uri.startswith("otpauth://totp/")
    assert secret in uri

def test_user_repository_totp_lifecycle(tmp_path):
    """Testa ciclo de ativação, validação e desativação de 2FA no UserRepository."""
    db_file = str(tmp_path / "test_user_totp.db")
    repo = UserRepository(db_file)
    assert repo.create_user("investidor_teste", "SenhaForte123!", role="investidor")
    user = repo.get_by_username("investidor_teste")
    assert user is not None
    user_id = user["id"]

    assert repo.is_totp_enabled(user_id) is False

    # Setup TOTP
    secret, uri = repo.setup_totp(user_id)
    assert len(secret) >= 26
    assert "otpauth://" in uri

    # Confirmação com código inválido falha
    assert repo.confirm_and_enable_totp(user_id, "999999") is False
    assert repo.is_totp_enabled(user_id) is False

    # Confirmação com código válido ativa
    valid_code = generate_totp_code(secret)
    assert repo.confirm_and_enable_totp(user_id, valid_code) is True
    assert repo.is_totp_enabled(user_id) is True

    # Validação durante login
    assert repo.verify_totp(user_id, valid_code) is True
    assert repo.verify_totp(user_id, "000000") is False

    # Desativação
    repo.disable_totp(user_id)
    assert repo.is_totp_enabled(user_id) is False

def test_budget_repository_dividends_received(tmp_path):
    """Testa persistência e consulta de proventos reais recebidos (P0)."""
    db_file = str(tmp_path / "test_dividends.db")
    repo = BudgetRepository(db_file)

    id1 = repo.record_dividend_received(
        ticker="BBAS3",
        data_pagamento="2026-03-15",
        valor_total=150.50,
        valor_por_cota=1.505,
        quantidade=100,
        tipo="DIVIDENDO"
    )
    id2 = repo.record_dividend_received(
        ticker="ITUB4",
        data_pagamento="2026-03-20",
        valor_total=80.00,
        tipo="JCP"
    )
    assert id1 > 0
    assert id2 > 0

    all_records = repo.list_dividends_received()
    assert len(all_records) == 2

    bbas3_records = repo.list_dividends_received(ticker="BBAS3")
    assert len(bbas3_records) == 1
    assert bbas3_records[0]["ticker"] == "BBAS3"
    assert bbas3_records[0]["valor_total"] == 150.50

    total_2026 = repo.get_total_dividends_received(year=2026)
    assert total_2026 == 230.50

    monthly = repo.get_dividends_summary_by_month(months=6)
    assert len(monthly) == 1
    assert monthly[0]["mes_ano"] == "2026-03"
    assert monthly[0]["total_mes"] == 230.50

def test_budget_repository_positions_custo_medio(tmp_path):
    """Garante que persist_positions e load_positions preservam custo_medio."""
    db_file = str(tmp_path / "test_pos_custo.db")
    repo = BudgetRepository(db_file)

    pos = [
        Position(ticker="BBAS3", row_index=10, preco_atual=28.0, div_proj=2.8, custo_medio=21.50),
        Position(ticker="TAEE11", row_index=11, preco_atual=35.0, div_proj=3.5, custo_medio=30.00)
    ]
    repo.save_positions(pos)

    loaded = repo.load_positions()
    assert len(loaded) == 2
    map_pos = {p.ticker: p for p in loaded}
    assert map_pos["BBAS3"].custo_medio == 21.50
    assert map_pos["TAEE11"].custo_medio == 30.00

def test_api_dividends_and_budget_endpoints(tmp_path, monkeypatch):
    """Testa endpoints REST de dividendos e orçamento."""
    test_db = str(tmp_path / "test_api_road.db")
    monkeypatch.setattr(settings, "sqlite_db_path", test_db)

    # Cria token admin
    token = create_session_token(1, "admin_test", "admin", settings.secret_key)
    client.cookies.set("barsi_session", token)

    # POST /api/dividends
    resp = client.post("/api/dividends", json={
        "ticker": "BBAS3",
        "data_pagamento": "2026-04-10",
        "valor_total": 210.0,
        "tipo": "DIVIDENDO"
    })
    assert resp.status_code == 200
    assert resp.json()["ok"] is True

    # GET /api/dividends
    resp_get = client.get("/api/dividends")
    assert resp_get.status_code == 200
    data = resp_get.json()
    assert data["total"] == 210.0
    assert len(data["records"]) == 1

    # GET /api/budget
    resp_budget = client.get("/api/budget")
    assert resp_budget.status_code == 200
    bdata = resp_budget.json()
    assert "total_entradas" in bdata
    assert "total_saidas" in bdata
    assert "saldo_livre" in bdata

    # GET /api/extratos/transacoes
    resp_ext = client.get("/api/extratos/transacoes")
    assert resp_ext.status_code == 200
    assert "transacoes" in resp_ext.json()

def test_api_2fa_endpoints_and_disable_security(tmp_path, monkeypatch):
    """Garante que a desativação do 2FA exige prova de posse (código TOTP válido)."""
    test_db = str(tmp_path / "test_api_2fa.db")
    monkeypatch.setattr(settings, "sqlite_db_path", test_db)
    from barsi.api import http
    monkeypatch.setattr(http, "_user_repo_cache", None)

    repo = UserRepository(test_db)
    repo.create_user("admin_2fa", "SenhaSegura123!", role="admin")
    user = repo.get_by_username("admin_2fa")
    user_id = user["id"]

    token = create_session_token(user_id, "admin_2fa", "admin", settings.secret_key)
    client.cookies.set("barsi_session", token)

    # 1. Setup 2FA
    res_setup = client.post("/api/auth/2fa/setup")
    assert res_setup.status_code == 200
    secret = res_setup.json()["secret"]

    # 2. Ativação com código inválido falha
    res_bad = client.post("/api/auth/2fa/enable", json={"code": "000000"})
    assert res_bad.status_code == 400

    # 3. Ativação com código válido
    valid_code = generate_totp_code(secret)
    res_ok = client.post("/api/auth/2fa/enable", json={"code": valid_code})
    assert res_ok.status_code == 200
    assert repo.is_totp_enabled(user_id) is True

    # 4. Desativação sem código válido FALHA (mitigação da Vulnerabilidade Crítica #2)
    res_dis_bad = client.post("/api/auth/2fa/disable", json={"code": "123456"})
    assert res_dis_bad.status_code == 400
    assert repo.is_totp_enabled(user_id) is True  # Permanece protegido!

    # 5. Desativação com código válido sucede
    res_dis_ok = client.post("/api/auth/2fa/disable", json={"code": valid_code})
    assert res_dis_ok.status_code == 200
    assert repo.is_totp_enabled(user_id) is False

def test_totp_failure_counts_as_rate_limit_attempt(tmp_path, monkeypatch):
    """Garante que tentativa de login com senha correta e TOTP incorreto ativa o rate limit (mitigação da Vulnerabilidade Crítica #1)."""
    test_db = str(tmp_path / "test_rate_totp.db")
    monkeypatch.setattr(settings, "sqlite_db_path", test_db)
    from barsi.api import http
    monkeypatch.setattr(http, "_user_repo_cache", None)
    http._login_attempts.clear()

    repo = UserRepository(test_db)
    repo.create_user("vitima_2fa", "SenhaVazadaCorreta!", role="investidor")
    user = repo.get_by_username("vitima_2fa")
    user_id = user["id"]

    # Ativa 2FA no usuário
    secret, _ = repo.setup_totp(user_id)
    repo.confirm_and_enable_totp(user_id, generate_totp_code(secret))
    assert repo.is_totp_enabled(user_id) is True

    # Realiza 5 tentativas com senha CORRETA mas TOTP ERRADO
    for i in range(5):
        resp = client.post(
            "/login",
            data={"username": "vitima_2fa", "password": "SenhaVazadaCorreta!", "totp_code": f"00000{i}"},
            follow_redirects=False
        )
        assert resp.status_code == 303
        assert "login_totp_failed" or "error=" in resp.headers.get("location", "")

    # 6ª tentativa deve ser BLOQUEADA pelo rate limiter (Muitas+tentativas)
    resp_blocked = client.post(
        "/login",
        data={"username": "vitima_2fa", "password": "SenhaVazadaCorreta!", "totp_code": "999999"},
        follow_redirects=False
    )
    assert resp_blocked.status_code == 303
    assert "Muitas+tentativas" in resp_blocked.headers.get("location", "")

def test_save_carteira_implausible_dy_rejected(tmp_path, monkeypatch):
    """Garante que DPA projetado que gere DY > 35% é rejeitado com HTTP 400 (evita bug de ITUB4/CMIG4 inflados)."""
    test_db = str(tmp_path / "test_dy_sanity.db")
    monkeypatch.setattr(settings, "sqlite_db_path", test_db)

    token = create_session_token(1, "admin_user", "admin", settings.secret_key)
    client.cookies.set("barsi_session", token)

    # ITUB4 a R$ 40,00 com DPA de R$ 15,00 (DY = 37.5% > 35%) deve ser REJEITADO
    resp = client.post("/api/carteira", json={
        "items": [{
            "ticker": "ITUB4",
            "row_index": 10,
            "preco_atual": 40.0,
            "div_proj": 15.0,
            "qtd_antes": 100
        }]
    })
    assert resp.status_code == 400
    assert "máx 35%" in resp.json()["detail"]

    # ITUB4 a R$ 40,00 com DPA realista de R$ 2,40 (DY = 6%) deve ser ACEITO
    resp_ok = client.post("/api/carteira", json={
        "items": [{
            "ticker": "ITUB4",
            "row_index": 10,
            "preco_atual": 40.0,
            "div_proj": 2.40,
            "qtd_antes": 100
        }]
    })
    assert resp_ok.status_code == 200

def test_telegram_webhook_flow(monkeypatch, tmp_path):
    """Testa webhook interativo do Telegram com whitelist de chat e resposta aos comandos."""
    test_db = str(tmp_path / "test_tg_db.db")
    monkeypatch.setattr(settings, "sqlite_db_path", test_db)
    monkeypatch.setattr(settings, "telegram_chat_id", "123456789")
    monkeypatch.setattr(settings, "telegram_bot_token", "fake_bot_token")
    monkeypatch.setattr(settings, "telegram_webhook_secret", "")

    repo = BudgetRepository(test_db)
    repo.save_positions([
        Position(ticker="BBAS3", row_index=10, preco_atual=25.0, div_proj=2.5, qtd_antes=100)
    ])

    # 1. Chat ID não autorizado é ignorado
    resp_unauth = client.post("/api/telegram/webhook", json={
        "message": {"chat": {"id": 9999999}, "text": "/status"}
    })
    assert resp_unauth.status_code == 200

    # 2. Comando /status autorizado
    resp_status = client.post("/api/telegram/webhook", json={
        "message": {"chat": {"id": 123456789}, "text": "/status"}
    })
    assert resp_status.status_code == 200

    # 3. Comando /radar autorizado
    resp_radar = client.post("/api/telegram/webhook", json={
        "message": {"chat": {"id": 123456789}, "text": "/radar"}
    })
    assert resp_radar.status_code == 200

    # 4. Comando /ticker BBAS3 autorizado
    resp_ticker = client.post("/api/telegram/webhook", json={
        "message": {"chat": {"id": 123456789}, "text": "/ticker BBAS3"}
    })
    assert resp_ticker.status_code == 200

def test_save_carteira_new_position_without_price_rejected(tmp_path, monkeypatch):
    """Garante que nova posição com div_proj sem preco_atual informado é rejeitada para evitar bypass do DY check."""
    test_db = str(tmp_path / "test_dy_new_pos.db")
    monkeypatch.setattr(settings, "sqlite_db_path", test_db)

    token = create_session_token(1, "admin_user", "admin", settings.secret_key)
    client.cookies.set("barsi_session", token)

    resp = client.post("/api/carteira", json={
        "items": [{
            "ticker": "NOVO3",
            "row_index": 20,
            "preco_atual": 0.0,
            "div_proj": 5.0,
            "qtd_antes": 50
        }]
    })
    assert resp.status_code == 400
    assert "informe preco_atual" in resp.json()["detail"].lower()

def test_telegram_webhook_secret_token_validation(monkeypatch, tmp_path):
    """Garante proteção de defense-in-depth via X-Telegram-Bot-Api-Secret-Token."""
    test_db = str(tmp_path / "test_tg_secret.db")
    monkeypatch.setattr(settings, "sqlite_db_path", test_db)
    monkeypatch.setattr(settings, "telegram_chat_id", "123456789")
    monkeypatch.setattr(settings, "telegram_webhook_secret", "segredo_secreto_super_forte_123")

    # 1. Requisição sem o header secreto é rejeitada com HTTP 403
    resp_no_secret = client.post(
        "/api/telegram/webhook",
        json={"message": {"chat": {"id": 123456789}, "text": "/status"}}
    )
    assert resp_no_secret.status_code == 403

    # 2. Requisição com secret incorreto é rejeitada com HTTP 403
    resp_bad_secret = client.post(
        "/api/telegram/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "secret_falso"},
        json={"message": {"chat": {"id": 123456789}, "text": "/status"}}
    )
    assert resp_bad_secret.status_code == 403

    # 3. Requisição com secret correto é aceita com HTTP 200
    resp_ok = client.post(
        "/api/telegram/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "segredo_secreto_super_forte_123"},
        json={"message": {"chat": {"id": 123456789}, "text": "/status"}}
    )
    assert resp_ok.status_code == 200

@pytest.mark.asyncio
async def test_telegram_webhook_run_command_success(monkeypatch, tmp_path):
    """Garante que o comando /run no Telegram executa sem AttributeError e processa actions_recommended."""
    test_db = str(tmp_path / "test_tg_run.db")
    monkeypatch.setattr(settings, "sqlite_db_path", test_db)
    monkeypatch.setattr(settings, "telegram_chat_id", "123456789")
    monkeypatch.setattr(settings, "telegram_webhook_secret", "")

    from barsi.domain.models import RunSummary
    from barsi.api import http

    class MockSyncService:
        async def execute_run(self):
            return RunSummary(
                run_id="run_mock_123",
                tickers_analyzed=10,
                actions_recommended={"ACUMULAR": 4, "AGUARDAR": 6, "MANTER": 0},
                duration_ms=450
            )

    replies = []
    async def mock_send_msg(token, chat_id, text):
        replies.append(text)

    monkeypatch.setattr(http, "get_sync_service", lambda: MockSyncService())
    monkeypatch.setattr(http, "send_telegram_message", mock_send_msg)
    http._telegram_run_lock = False

    resp = client.post(
        "/api/telegram/webhook",
        json={"message": {"chat": {"id": 123456789}, "text": "/run"}}
    )
    assert resp.status_code == 200
    assert any("disparado em segundo plano" in r for r in replies)
    assert any("Ciclo Concluído com Sucesso" in r for r in replies)
    assert http._telegram_run_lock is False

def test_telegram_webhook_run_command_blocks_concurrent(monkeypatch, tmp_path):
    """Garante que quando o lock está ativo, chamadas simultâneas ao /run são bloqueadas."""
    test_db = str(tmp_path / "test_tg_lock.db")
    monkeypatch.setattr(settings, "sqlite_db_path", test_db)
    monkeypatch.setattr(settings, "telegram_chat_id", "123456789")
    monkeypatch.setattr(settings, "telegram_webhook_secret", "")

    from barsi.api import http

    replies = []
    async def mock_send_msg(token, chat_id, text):
        replies.append(text)

    monkeypatch.setattr(http, "send_telegram_message", mock_send_msg)

    http._telegram_run_lock = True
    try:
        resp = client.post(
            "/api/telegram/webhook",
            json={"message": {"chat": {"id": 123456789}, "text": "/run"}}
        )
        assert resp.status_code == 200
        assert any("Já existe uma análise quantitativa em andamento" in r for r in replies)
    finally:
        http._telegram_run_lock = False

def test_list_users_includes_totp_enabled(tmp_path):
    """Garante que list_users retorna o campo totp_enabled corretamente."""
    db_file = str(tmp_path / "test_list_users_totp.db")
    repo = UserRepository(db_file)
    repo.create_user("user_sem_2fa", "SenhaForte123!", role="leitor")
    repo.create_user("user_com_2fa", "SenhaForte123!", role="investidor")

    u_com = repo.get_by_username("user_com_2fa")
    repo.setup_totp(u_com["id"])
    secret = repo.get_by_id(u_com["id"])["totp_secret"]
    valid_code = generate_totp_code(secret)
    repo.confirm_and_enable_totp(u_com["id"], valid_code)

    users = repo.list_users()
    users_map = {u["username"]: u for u in users}

    assert "totp_enabled" in users_map["user_sem_2fa"]
    assert bool(users_map["user_sem_2fa"]["totp_enabled"]) is False

    assert "totp_enabled" in users_map["user_com_2fa"]
    assert bool(users_map["user_com_2fa"]["totp_enabled"]) is True

def test_health_sync_and_reconcile_endpoints(tmp_path, monkeypatch):
    """Garante que /api/health/sync e /api/health/sync/reconcile operam no Path B sem 404."""
    test_db = str(tmp_path / "test_parity.db")
    monkeypatch.setattr(settings, "sqlite_db_path", test_db)

    token = create_session_token(1, "admin_user", "admin", settings.secret_key)
    client.cookies.set("barsi_session", token)

    repo = BudgetRepository(test_db)
    pos = Position(ticker="ITUB4", row_index=2, preco_atual=30.0, div_proj=2.40, qtd_antes=100)
    repo.save_positions([pos])

    resp = client.get("/api/health/sync")
    assert resp.status_code == 200
    data = resp.json()
    assert "in_sync" in data
    assert "tickers_checked" in data

    resp_rec = client.post("/api/health/sync/reconcile?master=sqlite")
    assert resp_rec.status_code == 200
    assert resp_rec.json()["ok"] is True

def test_cli_run_uses_budget_repo_and_persists_sqlite(tmp_path, monkeypatch):
    """Garante que 'python -m barsi run' consome e grava no SQLite canônico (Path B)."""
    from typer.testing import CliRunner
    from barsi.__main__ import app
    from barsi.domain.models import MarketQuote

    test_db = str(tmp_path / "test_cli_run.db")
    monkeypatch.setattr(settings, "sqlite_db_path", test_db)
    monkeypatch.setattr(settings, "telegram_alerts_enabled", False)

    repo = BudgetRepository(test_db)
    pos = Position(
        ticker="BBAS3",
        row_index=2,
        empresa="Banco do Brasil",
        setor="Bancos",
        preco_atual=25.00,
        preco_teto=35.00,
        div_proj=2.10,
        status_barsi="ACUMULAR",
        margem_seguranca=28.57,
        qtd_antes=100
    )
    repo.save_positions([pos])

    # Mock market quote para retornar cotação atualizada
    class MockMarket:
        async def get_quote(self, ticker: str):
            return MarketQuote(ticker=ticker, price=27.50)

    from barsi.adapters.factory import AdapterFactory
    monkeypatch.setattr(AdapterFactory, "create_market_adapter", lambda s: MockMarket())

    runner = CliRunner()
    result = runner.invoke(app, ["run", "--no-radar"])
    assert result.exit_code == 0, result.output
    assert "Run concluída!" in result.output

    # Verifica se o SQLite foi atualizado com a nova cotação
    updated_pos = {p.ticker: p for p in repo.load_positions()}
    assert "BBAS3" in updated_pos
    assert updated_pos["BBAS3"].preco_atual == 27.50
    assert updated_pos["BBAS3"].preco_teto > 0

@pytest.mark.asyncio
async def test_send_hourly_radar_summary_triggered_by_notify_radar(tmp_path, monkeypatch):
    """Garante que execute_run dispara send_hourly_radar_summary quando notify_radar=True."""
    from barsi.services.sync_service import SyncService
    from barsi.domain.models import MarketQuote, NewsSummary, InsightResult

    test_db = str(tmp_path / "test_radar.db")
    repo = BudgetRepository(test_db)
    pos = Position(
        ticker="BBAS3",
        row_index=2,
        empresa="Banco do Brasil",
        setor="Bancos",
        preco_atual=20.00,
        preco_teto=35.00,
        div_proj=2.10,
        status_barsi="ACUMULAR",
        margem_seguranca=42.8,
        qtd_antes=100
    )
    repo.save_positions([pos])

    notifications = []
    class MockNotifier:
        async def notify(self, title, body, level="info"):
            notifications.append((title, body, level))
            return True

    class MockMarket:
        async def get_quote(self, ticker: str):
            return MarketQuote(ticker=ticker, price=20.00)

    class MockNews:
        async def get_latest_news(self, ticker: str):
            return NewsSummary(ticker=ticker, headlines="")

    class MockAI:
        async def generate_insight(self, *args, **kwargs):
            return InsightResult(ticker="BBAS3", insight="Bom ativo", acao_sugerida="ACUMULAR", provider="rule_engine")

    class MockSheets:
        async def load_positions(self): return []
        async def update_positions(self, p): pass

    class MockHistory:
        async def get_last_analysis(self, t): return None
        async def save_analysis(self, **kw): pass
        async def record_run(self, s): pass

    service = SyncService(
        market=MockMarket(),
        news=MockNews(),
        ai=MockAI(),
        sheets=MockSheets(),
        history=MockHistory(),
        notifier=MockNotifier(),
        budget_repo=repo
    )

    await service.execute_run(dry_run=False, notify_radar=True)
    # Deve conter notificação de radar consolidado
    radar_msgs = [m for m in notifications if "Radar" in m[0]]
    assert len(radar_msgs) == 1
    assert "BBAS3" in radar_msgs[0][1]

def test_max_plausible_dy_setting(tmp_path, monkeypatch):
    """Garante que settings.max_plausible_dy é respeitado na validação de DY."""
    test_db = str(tmp_path / "test_dy_setting.db")
    monkeypatch.setattr(settings, "sqlite_db_path", test_db)
    monkeypatch.setattr(settings, "max_plausible_dy", 0.50)  # Limite tolerante de 50%

    token = create_session_token(1, "admin_user", "admin", settings.secret_key)
    client.cookies.set("barsi_session", token)

    repo = BudgetRepository(test_db)
    pos = Position(ticker="MXRF11", row_index=2, preco_atual=10.0, div_proj=1.0, qtd_antes=100)
    repo.save_positions([pos])

    # 40% de DY deve passar com limite de 50%
    payload_ok = {"items": [{"ticker": "MXRF11", "row_index": 2, "div_proj": 4.0, "preco_atual": 10.0}]}
    resp_ok = client.post("/api/carteira", json=payload_ok)
    assert resp_ok.status_code == 200

    # 60% de DY deve falhar com limite de 50%
    payload_fail = {"items": [{"ticker": "MXRF11", "row_index": 2, "div_proj": 6.0, "preco_atual": 10.0}]}
    resp_fail = client.post("/api/carteira", json=payload_fail)
    assert resp_fail.status_code == 400
    assert "excede o limite aceitável" in resp_fail.json()["detail"] or "resulta em DY" in resp_fail.json()["detail"]

def test_health_sync_detects_multiple_field_divergences(tmp_path, monkeypatch):
    """Garante que /api/health/sync detecta divergências em múltiplos campos (preco_teto, margem_seguranca, etc)."""
    test_db = str(tmp_path / "test_divergences.db")
    monkeypatch.setattr(settings, "sqlite_db_path", test_db)

    token = create_session_token(1, "admin_user", "admin", settings.secret_key)
    client.cookies.set("barsi_session", token)

    repo = BudgetRepository(test_db)
    pos_sql = Position(
        ticker="CMIG4",
        row_index=2,
        preco_atual=10.00,
        preco_teto=15.00,
        div_proj=0.90,
        margem_seguranca=33.3,
        qtd_antes=500
    )
    repo.save_positions([pos_sql])

    pos_sheet = Position(
        ticker="CMIG4",
        row_index=2,
        preco_atual=10.00,
        preco_teto=20.00,  # Divergência no preço teto
        div_proj=0.90,
        margem_seguranca=50.0,  # Divergência na margem
        qtd_antes=500
    )

    class MockSheetsWithDivergence:
        async def load_positions(self):
            return [pos_sheet]

    from barsi.adapters.factory import AdapterFactory
    monkeypatch.setattr(AdapterFactory, "create_sheets_adapter", lambda s: MockSheetsWithDivergence())

    resp = client.get("/api/health/sync")
    assert resp.status_code == 200
    data = resp.json()
    assert data["in_sync"] is False
    assert len(data["divergences"]) == 1
    diffs = data["divergences"][0]["divergences"]
    assert any("preco_teto" in d for d in diffs)
    assert any("margem_seguranca" in d for d in diffs)

def test_scheduler_default_config(tmp_path):
    repo = BudgetRepository(str(tmp_path / "t.db"))
    cfg = repo.get_scheduler_config()
    assert cfg["enabled"] == 1
    assert cfg["days_of_week"] == "1,2,3,4,5"
    assert "9" in cfg["hours"].split(",")
    assert cfg["minute"] == 0

def test_scheduler_is_run_due_respects_disabled(tmp_path):
    repo = BudgetRepository(str(tmp_path / "t.db"))
    repo.update_scheduler_config(enabled=False)
    should_run, reason = repo.is_run_due()
    assert should_run is False
    assert reason == "disabled"

def test_scheduler_is_run_due_respects_day_and_hour(tmp_path):
    repo = BudgetRepository(str(tmp_path / "t.db"))
    # Segunda-feira 10:00, minuto 0 (2026-11-09 é segunda)
    segunda_10h = datetime(2026, 11, 9, 10, 0)
    should_run, reason = repo.is_run_due(now=segunda_10h)
    assert should_run is True, reason

    # Sábado 10:00 -> fora dos dias de semana
    sabado_10h = datetime(2026, 11, 7, 10, 0)
    should_run, reason = repo.is_run_due(now=sabado_10h)
    assert should_run is False
    assert reason == "outside_days"

    # Segunda-feira 3:00 -> fora do pregão
    segunda_3h = datetime(2026, 11, 9, 3, 0)
    should_run, reason = repo.is_run_due(now=segunda_3h)
    assert should_run is False
    assert reason == "outside_hours"

def test_scheduler_dedup_within_20min(tmp_path):
    import sqlite3
    repo = BudgetRepository(str(tmp_path / "t.db"))
    segunda_10h = datetime(2026, 11, 9, 10, 0)
    run_time = (segunda_10h - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")

    with sqlite3.connect(repo.db_path) as c:
        c.execute(
            "INSERT INTO runs (run_id, timestamp, duration_ms, tickers_analyzed, memory_peak_mb, errors) "
            "VALUES (?, ?, 100, 10, 110, '')",
            ("recent", run_time),
        )
    should_run, reason = repo.is_run_due(now=segunda_10h)
    assert should_run is False
    assert reason == "already_ran_recently"

def test_scheduler_endpoint_requires_admin(tmp_path, monkeypatch):
    test_db = str(tmp_path / "t.db")
    monkeypatch.setattr(settings, "sqlite_db_path", test_db)
    
    # Usuário comum (leitor)
    token_leitor = create_session_token(2, "leitor_user", "leitor", settings.secret_key)
    client.cookies.set("barsi_session", token_leitor)
    assert client.post("/api/scheduler", json={"enabled": False}).status_code == 403

    # Administrador
    token_admin = create_session_token(1, "admin_user", "admin", settings.secret_key)
    client.cookies.set("barsi_session", token_admin)
    res = client.post("/api/scheduler", json={"enabled": False})
    assert res.status_code == 200
    assert res.json()["config"]["enabled"] is False

def test_scheduler_next_run_computed(tmp_path):
    repo = BudgetRepository(str(tmp_path / "t.db"))
    repo.update_scheduler_config(
        days_of_week="1,2,3,4,5",
        hours="9,10",
        minute=0,
    )
    # Sexta-feira 11:00 -> próximo slot deve ser na segunda-feira seguinte às 09:00
    sexta_11h = datetime(2026, 11, 13, 11, 0)
    next_run = repo.next_scheduled_run(now=sexta_11h)
    assert next_run is not None
    assert next_run.startswith("2026-11-16T09:00")

def test_notification_prefs_default_enabled(tmp_path):
    repo = BudgetRepository(str(tmp_path / "t.db"))
    assert repo.is_notification_enabled("margem_20") is True
    assert repo.is_notification_enabled("radar_summary") is True
    assert repo.is_notification_enabled("desconhecido") is True  # fail-open

def test_notification_prefs_disable_blocks_should_notify(tmp_path):
    repo = BudgetRepository(str(tmp_path / "t.db"))
    repo.set_notification_enabled("margem_20", False, updated_by="test")
    assert repo.should_notify("BBAS3", "margem_20") is False

    # Re-enable e confirma que dedup volta a funcionar
    repo.set_notification_enabled("margem_20", True)
    assert repo.should_notify("BBAS3", "margem_20") is True
    assert repo.should_notify("BBAS3", "margem_20") is False  # já notificou hoje

def test_notifications_telegram_command(tmp_path, monkeypatch):
    test_db = str(tmp_path / "test_notif_tg.db")
    monkeypatch.setattr(settings, "sqlite_db_path", test_db)
    monkeypatch.setattr(settings, "telegram_chat_id", "123456789")
    monkeypatch.setattr(settings, "telegram_bot_token", "fake_bot_token")
    monkeypatch.setattr(settings, "telegram_webhook_secret", "")

    replies = []
    async def mock_send(token, chat_id, text, parse_mode="Markdown"):
        replies.append(text)
        return True

    from barsi.api import http
    monkeypatch.setattr(http, "send_telegram_message", mock_send)

    # 1. Menu de preferências
    payload = {
        "message": {
            "chat": {"id": 123456789},
            "text": "/notifications"
        }
    }
    resp = client.post("/api/telegram/webhook", json=payload)
    assert resp.status_code == 200
    assert any("Preferências de Notificação" in r for r in replies)

    # 2. Desativa margem_20
    payload_off = {
        "message": {
            "chat": {"id": 123456789},
            "text": "/notifications margem_20 off"
        }
    }
    resp_off = client.post("/api/telegram/webhook", json=payload_off)
    assert resp_off.status_code == 200
    repo = BudgetRepository(test_db)
    assert repo.is_notification_enabled("margem_20") is False

    # 3. Testa comando /schedule
    payload_sched = {
        "message": {
            "chat": {"id": 123456789},
            "text": "/schedule"
        }
    }
    resp_sched = client.post("/api/telegram/webhook", json=payload_sched)
    assert resp_sched.status_code == 200
    assert any("Scheduler Barsi" in r for r in replies)

def test_cli_run_if_due_skips_when_not_due(tmp_path, monkeypatch):
    from typer.testing import CliRunner
    from barsi.__main__ import app

    test_db = str(tmp_path / "test_if_due.db")
    monkeypatch.setattr(settings, "sqlite_db_path", test_db)

    repo = BudgetRepository(test_db)
    # Desativa scheduler para garantir que não deve rodar
    repo.update_scheduler_config(enabled=False)

    runner = CliRunner()
    result = runner.invoke(app, ["run", "--if-due"])
    assert result.exit_code == 0
    assert "Run não devida: disabled" in result.output

def test_scheduler_dedup_with_real_iso_format(tmp_path):
    """Garante que dedup funciona com timestamps ISO (formato real do SQLiteHistoryAdapter)."""
    import sqlite3
    repo = BudgetRepository(str(tmp_path / "t.db"))
    segunda_10h = datetime(2026, 11, 9, 10, 0)

    # Grava no formato REAL (isoformat com 'T'), simulando SQLiteHistoryAdapter.record_run
    run_time = (segunda_10h - timedelta(minutes=5)).isoformat()
    with sqlite3.connect(repo.db_path) as c:
        c.execute(
            "INSERT INTO runs (run_id, timestamp, duration_ms, tickers_analyzed, memory_peak_mb, errors) "
            "VALUES (?, ?, 100, 10, 110, '')",
            ("recent", run_time),
        )

    # 1. Dentro da janela de 20 min -> deve pular
    should_run, reason = repo.is_run_due(now=segunda_10h)
    assert should_run is False
    assert reason == "already_ran_recently"

    # 2. Na hora seguinte (11:00) -> NÃO deve bloquear (janela expirada)
    onze_horas = segunda_10h.replace(hour=11)
    should_run_11, reason_11 = repo.is_run_due(now=onze_horas)
    assert should_run_11 is True, f"Falso positivo de dedup com timestamp ISO: {reason_11}"

def test_notification_prefs_invalid_alert_type_rejected(tmp_path, monkeypatch):
    """Garante que tipos desconhecidos de notificação são rejeitados."""
    test_db = str(tmp_path / "test_notif_val.db")
    monkeypatch.setattr(settings, "sqlite_db_path", test_db)
    repo = BudgetRepository(test_db)

    with pytest.raises(ValueError, match="alert_type inválido"):
        repo.set_notification_enabled("tipo_fantasma", True)

    token = create_session_token(1, "admin_user", "admin", settings.secret_key)
    client.cookies.set("barsi_session", token)
    res = client.post("/api/notifications/preferences", json={"alert_type": "fantasma", "enabled": True})
    assert res.status_code == 400
    assert "alert_type inválido" in res.json()["detail"]

@pytest.mark.asyncio
async def test_send_hourly_radar_summary_force_parameter(tmp_path):
    """Garante que force=True envia radar mesmo com radar_summary desabilitado nas preferências."""
    from barsi.services.sync_service import SyncService
    from barsi.domain.models import MarketQuote, NewsSummary, InsightResult

    test_db = str(tmp_path / "test_force_radar.db")
    repo = BudgetRepository(test_db)
    # Desabilita preferência de radar
    repo.set_notification_enabled("radar_summary", False)

    pos = Position(
        ticker="BBAS3",
        row_index=2,
        empresa="Banco do Brasil",
        setor="Bancos",
        preco_atual=20.00,
        preco_teto=35.00,
        div_proj=2.10,
        status_barsi="ACUMULAR",
        margem_seguranca=42.8,
        qtd_antes=100
    )
    repo.save_positions([pos])

    notifications = []
    class MockNotifier:
        async def notify(self, title, body, level="info"):
            notifications.append((title, body, level))
            return True

    class MockMarket:
        async def get_quote(self, ticker: str):
            return MarketQuote(ticker=ticker, price=20.00)

    class MockNews:
        async def get_latest_news(self, ticker: str):
            return NewsSummary(ticker=ticker, headlines="")

    class MockAI:
        async def generate_insight(self, *args, **kwargs):
            return InsightResult(ticker="BBAS3", insight="Bom ativo", acao_sugerida="ACUMULAR", provider="rule_engine")

    class MockSheets:
        async def load_positions(self): return []
        async def update_positions(self, p): pass

    class MockHistory:
        async def get_last_analysis(self, t): return None
        async def save_analysis(self, **kw): pass
        async def record_run(self, s): pass

    service = SyncService(
        market=MockMarket(),
        news=MockNews(),
        ai=MockAI(),
        sheets=MockSheets(),
        history=MockHistory(),
        notifier=MockNotifier(),
        budget_repo=repo
    )

    # 1. Sem force (ex: automação periódica): respeita preferência desabilitada
    await service.execute_run(dry_run=False, notify_radar=True, force_radar=False)
    radar_msgs = [m for m in notifications if "Radar" in m[0]]
    assert len(radar_msgs) == 0

    # 2. Com force=True (ex: CLI manual --radar): sobrepõe e envia
    await service.execute_run(dry_run=False, notify_radar=True, force_radar=True)
    radar_msgs_forced = [m for m in notifications if "Radar" in m[0]]
    assert len(radar_msgs_forced) == 1
    assert "BBAS3" in radar_msgs_forced[0][1]








