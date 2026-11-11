"""Testes automatizados das funcionalidades do Roadmap (P0, 2FA TOTP, Dividends & YoC)."""
import os
import pytest
from fastapi.testclient import TestClient

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




