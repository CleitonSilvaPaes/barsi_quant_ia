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
    secret = generate_totp_secret()
    assert len(secret) >= 26
    
    code = generate_totp_code(secret)
    assert len(code) == 6
    assert code.isdigit()
    
    # Verificação com código correto
    assert verify_totp_code(secret, code) is True
    # Verificação com código incorreto
    assert verify_totp_code(secret, "000000" if code != "000000" else "111111") is False

    uri = get_totp_uri(secret, "cleiton")
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
