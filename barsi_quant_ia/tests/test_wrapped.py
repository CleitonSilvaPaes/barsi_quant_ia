"""Testes automatizados para o Barsi Wrapped (Retrospectiva Anual).

Valida cálculo de métricas anuais, isolamento por ano, badges Barsi,
formatação do card Telegram e endpoints REST.
"""

from __future__ import annotations

import pytest
import respx
from starlette.testclient import TestClient

from barsi.adapters.factory import AdapterFactory
from barsi.adapters.history.budget_repository import BudgetRepository
from barsi.adapters.history.user_repository import UserRepository
from barsi.api.http import app
from barsi.config import settings
from barsi.domain.models import Position
from barsi.infra.security import create_session_token
from barsi.services.wrapped_service import WrappedService


class MockSheetsAdapter:
    def __init__(self, positions=None):
        self.positions = positions or [
            Position(
                ticker="BBAS3",
                row_index=1,
                empresa="Banco do Brasil",
                setor="BANCOS",
                preco_atual=28.0,
                div_proj=2.8,
                qtd_antes=200,
                custo_medio=22.0,
            ),
            Position(
                ticker="TAEE11",
                row_index=2,
                empresa="Taesa",
                setor="ENERGIA",
                preco_atual=35.0,
                div_proj=3.5,
                qtd_antes=150,
                custo_medio=30.0,
            ),
            Position(
                ticker="SAPR4",
                row_index=3,
                empresa="Sanepar",
                setor="SANEAMENTO",
                preco_atual=5.2,
                div_proj=0.45,
                qtd_antes=1000,
                custo_medio=4.5,
            ),
        ]

    async def load_positions(self):
        return self.positions

    async def update_positions(self, positions, invalidate=True):
        self.positions = positions


class MockTelegramNotifier:
    def __init__(self):
        self.sent_notifications = []

    async def notify(self, title: str, body: str, level: str = "info") -> bool:
        self.sent_notifications.append({"title": title, "body": body, "level": level})
        return True


def test_wrapped_service_annual_metrics_and_year_isolation(tmp_path):
    """Valida cálculo de proventos do ano, média, melhor mês e isolamento entre anos."""
    db_file = str(tmp_path / "test_wrapped_metrics.db")
    repo = BudgetRepository(db_file)

    # Inserção de proventos em 2025
    repo.record_dividend_received(ticker="BBAS3", data_pagamento="2025-05-15", valor_total=300.0)
    repo.record_dividend_received(ticker="BBAS3", data_pagamento="2025-11-20", valor_total=400.0)

    # Inserção de proventos em 2026 (meses diferentes)
    repo.record_dividend_received(ticker="BBAS3", data_pagamento="2026-03-15", valor_total=500.0)
    repo.record_dividend_received(ticker="TAEE11", data_pagamento="2026-05-20", valor_total=750.0)
    repo.record_dividend_received(ticker="SAPR4", data_pagamento="2026-08-10", valor_total=250.0)
    repo.record_dividend_received(ticker="BBAS3", data_pagamento="2026-11-25", valor_total=600.0)

    # Snapshots para 2026
    repo.save_portfolio_snapshot("2026-01-31", patrimonio_total=50000.0, cotas_totais=1000)
    repo.save_portfolio_snapshot("2026-12-31", patrimonio_total=62000.0, cotas_totais=1250)

    adapter = MockSheetsAdapter()
    positions = adapter.positions

    # Análise de 2026
    res_2026 = WrappedService.get_annual_retrospective(2026, repo, positions)

    assert res_2026["year"] == 2026
    assert res_2026["total_proventos"] == 2100.0  # 500 + 750 + 250 + 600
    assert res_2026["media_mensal"] == round(2100.0 / 12.0, 2)
    assert res_2026["meses_com_renda"] == 4  # Março, Maio, Agosto, Novembro

    # Melhor mês deve ser Maio (TAEE11 = 750.0)
    assert res_2026["melhor_mes"]["mes"] == "05"
    assert res_2026["melhor_mes"]["nome"] == "Maio"
    assert res_2026["melhor_mes"]["total"] == 750.0

    # Campeão do ano deve ser BBAS3 (500 + 600 = 1100.0)
    assert res_2026["campeao"]["ticker"] == "BBAS3"
    assert res_2026["campeao"]["total"] == 1100.0
    assert res_2026["campeao"]["percentual"] == round(1100.0 / 2100.0 * 100.0, 1)

    # Snapshots
    assert res_2026["patrimonio_inicial"] == 50000.0
    assert res_2026["patrimonio_final"] == 62000.0
    assert res_2026["variacao_patrimonio"] == 12000.0
    assert res_2026["variacao_cotas"] == 250

    # Isolamento: 2025 NÃO deve conter os dados de 2026
    res_2025 = WrappedService.get_annual_retrospective(2025, repo, positions)
    assert res_2025["year"] == 2025
    assert res_2025["total_proventos"] == 700.0
    assert res_2025["meses_com_renda"] == 2


def test_wrapped_badges_and_snowball(tmp_path):
    """Valida cálculo do efeito bola de neve e desbloqueio de badges."""
    db_file = str(tmp_path / "test_wrapped_badges.db")
    repo = BudgetRepository(db_file)

    # Insere 10 meses de proventos para desbloquear o Relógio Suíço
    for m in range(1, 11):
        mes_str = f"2026-{m:02d}-15"
        repo.record_dividend_received(ticker="BBAS3", data_pagamento=mes_str, valor_total=250.0)

    adapter = MockSheetsAdapter()
    positions = adapter.positions

    res = WrappedService.get_annual_retrospective(2026, repo, positions)
    badges = {b["id"]: b for b in res["badges"]}

    # Relógio Suíço deve estar desbloqueado (10 meses com proventos)
    assert badges["relogio_suico"]["desbloqueado"] is True

    # Fortaleza B.E.S.T. deve estar desbloqueado (todas as ações do mock são B.E.S.T.)
    assert res["pct_best"] == 100.0
    assert badges["fortaleza_best"]["desbloqueado"] is True

    # Bola de Neve: 2500 total / 28.0 (preço BBAS3) = 89 cotas
    assert res["bola_de_neve"]["cotas_compradas"] >= 20
    assert badges["bola_de_neve"]["desbloqueado"] is True

    # Rei dos Dividendos (BBAS3 gerou 2500.0 >= 200)
    assert badges["rei_dividendos"]["desbloqueado"] is True


def test_wrapped_telegram_card_formatting(tmp_path):
    """Valida montagem do card Markdown para o bot do Telegram."""
    db_file = str(tmp_path / "test_wrapped_card.db")
    repo = BudgetRepository(db_file)
    repo.record_dividend_received(ticker="TAEE11", data_pagamento="2026-06-15", valor_total=1500.0)

    adapter = MockSheetsAdapter()
    data = WrappedService.get_annual_retrospective(2026, repo, adapter.positions)

    card = WrappedService.generate_telegram_card(data)
    assert "🎁 *Barsi Wrapped 2026 — Retrospectiva Anual*" in card
    assert "Total de Proventos:* R$ 1.500,00" in card
    assert "TAEE11" in card
    assert "Alocação B.E.S.T.:" in card
    assert "Luiz Barsi Filho" in card


def test_wrapped_api_endpoints_auth_and_data(tmp_path, monkeypatch):
    """Valida autenticação e resposta JSON dos endpoints GET e POST /api/wrapped."""
    db_file = str(tmp_path / "test_wrapped_api.db")
    monkeypatch.setattr(settings, "sqlite_db_path", db_file)
    monkeypatch.setattr(settings, "telegram_alerts_enabled", True)

    repo = BudgetRepository(db_file)
    user_repo = UserRepository(db_file)
    user_repo.create_user("investidor_teste", "senhaSegura123", role="investidor")

    repo.record_dividend_received(ticker="BBAS3", data_pagamento="2026-04-10", valor_total=850.0)

    mock_notifier = MockTelegramNotifier()
    monkeypatch.setattr(AdapterFactory, "create_notification_adapter", lambda s: mock_notifier)
    mock_adapter = MockSheetsAdapter()
    monkeypatch.setattr(AdapterFactory, "create_sheets_adapter", lambda s: mock_adapter)

    client = TestClient(app)

    # 1. Sem autenticação deve retornar 401
    resp_unauth = client.get("/api/wrapped/2026")
    assert resp_unauth.status_code == 401

    # 2. Com autenticação
    session_id = "test_wrapped_jti_123"
    token = create_session_token(
        user_id=1,
        username="investidor_teste",
        role="investidor",
        secret_key=settings.secret_key,
        expires_in_sec=3600,
        jti=session_id,
    )
    client.cookies.set("barsi_session", token)

    resp_auth = client.get("/api/wrapped/2026")
    assert resp_auth.status_code == 200
    data = resp_auth.json()
    assert data["year"] == 2026
    assert data["total_proventos"] == 850.0
    assert len(data["badges"]) >= 6

    # 3. Disparo para Telegram via POST
    csrf = user_repo.rotate_session_csrf(session_id, user_id=1)
    client.cookies.set("barsi_csrf", csrf)
    resp_post = client.post(
        "/api/wrapped/2026/telegram",
        headers={"X-CSRF-Token": csrf},
    )
    assert resp_post.status_code == 200
    post_json = resp_post.json()
    assert post_json["ok"] is True
    assert len(mock_notifier.sent_notifications) == 1
    assert "Barsi Wrapped 2026" in mock_notifier.sent_notifications[0]["title"]


def test_wrapped_telegram_bot_command(tmp_path, monkeypatch):
    """Valida o comando /wrapped via webhook interativo do bot Telegram."""
    db_file = str(tmp_path / "test_wrapped_tg_bot.db")
    monkeypatch.setattr(settings, "sqlite_db_path", db_file)
    monkeypatch.setattr(settings, "telegram_chat_id", "12345678")
    monkeypatch.setattr(settings, "telegram_bot_token", "fake_bot_token")
    monkeypatch.setattr(settings, "telegram_webhook_secret", "")

    repo = BudgetRepository(db_file)
    repo.record_dividend_received(ticker="BBAS3", data_pagamento="2026-07-20", valor_total=1200.0)

    mock_adapter = MockSheetsAdapter()
    monkeypatch.setattr(AdapterFactory, "create_sheets_adapter", lambda s: mock_adapter)

    captured_replies = []

    async def mock_send_telegram(token, chat_id, text):
        captured_replies.append({"chat_id": chat_id, "text": text})

    import barsi.api.http as http_mod
    import barsi.api.routers.telegram as tg_router_mod
    monkeypatch.setattr(http_mod, "send_telegram_message", mock_send_telegram)
    monkeypatch.setattr(tg_router_mod, "send_telegram_message", mock_send_telegram)

    client = TestClient(app)

    # Dispara comando /wrapped 2026
    payload = {
        "message": {
            "chat": {"id": 12345678},
            "text": "/wrapped 2026",
        }
    }
    resp = client.post("/api/telegram/webhook", json=payload)
    assert resp.status_code == 200

    # Deve ter enviado a mensagem comemorativa do Wrapped
    assert len(captured_replies) >= 2
    final_reply = captured_replies[-1]["text"]
    assert "Barsi Wrapped 2026" in final_reply
    assert "Total de Proventos:* R$ 1.200,00" in final_reply


def test_wrapped_fallback_counts_ofx_dividends_only(tmp_path):
    """Se dividends_received está vazio mas OFX tem transações de provento, devem ser contadas."""
    db_file = str(tmp_path / "test_wrapped_ofx_fallback.db")
    repo = BudgetRepository(db_file)

    # Nenhuma entrada em dividends_received
    assert repo.get_total_dividends_received(year=2026) == 0.0

    # Transação OFX de provento inserida
    repo.persist_transacoes([
        {
            "id_transacao": "fit-div-01",
            "data": "2026-06-15",
            "tipo": "CREDIT",
            "valor": 500.0,
            "descricao": "DIVIDENDO BBAS3",
            "categoria": "Proventos B3",
            "ticker_relacionado": "BBAS3",
        },
        {
            "id_transacao": "fit-div-02",
            "data": "2026-09-20",
            "tipo": "CREDIT",
            "valor": 300.0,
            "descricao": "JCP TAEE11",
            "categoria": "Juros Sobre Capital",
            "ticker_relacionado": "TAEE11",
        },
    ])

    adapter = MockSheetsAdapter()
    res = WrappedService.get_annual_retrospective(2026, repo, adapter.positions)

    assert res["total_proventos"] == 800.0
    assert res["meses_com_renda"] == 2
    assert res["campeao"]["ticker"] == "BBAS3"
    assert res["campeao"]["total"] == 500.0


@respx.mock
def test_wrapped_telegram_uses_real_notifier(tmp_path, monkeypatch):
    """Garante que o endpoint POST /api/wrapped/{year}/telegram chama notifier.notify com kwargs corretos (level=, não severity=)."""
    db_file = str(tmp_path / "test_wrapped_real_tg.db")
    monkeypatch.setattr(settings, "sqlite_db_path", db_file)
    monkeypatch.setattr(settings, "telegram_alerts_enabled", True)
    monkeypatch.setattr(settings, "telegram_chat_id", "998877")
    monkeypatch.setattr(settings, "telegram_bot_token", "fake_bot_token_123")

    repo = BudgetRepository(db_file)
    user_repo = UserRepository(db_file)
    user_repo.create_user("investidor_real", "senhaForte123", role="investidor")
    repo.record_dividend_received(ticker="BBAS3", data_pagamento="2026-05-10", valor_total=950.0)

    mock_adapter = MockSheetsAdapter()
    monkeypatch.setattr(AdapterFactory, "create_sheets_adapter", lambda s: mock_adapter)

    # Mocka a chamada HTTP real à API do Telegram
    respx.post("https://api.telegram.org/botfake_bot_token_123/sendMessage").respond(
        200, json={"ok": True}
    )

    client = TestClient(app)
    session_id = "test_real_notifier_jti"
    token = create_session_token(
        user_id=1,
        username="investidor_real",
        role="investidor",
        secret_key=settings.secret_key,
        expires_in_sec=3600,
        jti=session_id,
    )
    client.cookies.set("barsi_session", token)

    csrf = user_repo.rotate_session_csrf(session_id, user_id=1)
    client.cookies.set("barsi_csrf", csrf)

    resp = client.post(
        "/api/wrapped/2026/telegram",
        headers={"X-CSRF-Token": csrf},
    )
    # Com o fix level=, status deve ser 200 (não 500)
    assert resp.status_code == 200
    res_json = resp.json()
    assert res_json["ok"] is True
    assert "Barsi Wrapped 2026" in res_json["card"]


def test_wrapped_html_page_render_and_dynamic_years():
    """Valida renderização da página HTML /wrapped e presença da função de anos dinâmicos."""
    client = TestClient(app)
    session_id = "test_page_render_jti"
    token = create_session_token(
        user_id=1,
        username="investidor_teste",
        role="investidor",
        secret_key=settings.secret_key,
        expires_in_sec=3600,
        jti=session_id,
    )
    client.cookies.set("barsi_session", token)

    resp = client.get("/wrapped")
    assert resp.status_code == 200
    assert "Barsi Wrapped — Retrospectiva Anual" in resp.text
    assert "popularAnosWrapped" in resp.text
    assert "wrapped-year-select" in resp.text

