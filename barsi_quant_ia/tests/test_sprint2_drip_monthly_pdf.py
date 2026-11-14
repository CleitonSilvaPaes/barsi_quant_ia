"""Testes automatizados da Sprint 2 do Roadmap Barsi Quant IA v3.0:
- Reinvestimento Automático de Dividendos (DRIP Barsi)
- Fechamento Mensal Automatizado (Relatório Executivo PDF & HTML Preview)
- Endpoints REST API (/api/drip/simulate, /api/drip/apply, /api/reports/monthly-closing)
"""
import os
import json
import pytest
from datetime import datetime
from fastapi.testclient import TestClient

from barsi.domain.models import Position, DividendReceived
from barsi.adapters.history.budget_repository import BudgetRepository
from barsi.adapters.history.user_repository import UserRepository
from barsi.services.drip_service import DripService
from barsi.services.monthly_closing_service import MonthlyClosingService
from barsi.infra.security import create_session_token
from barsi.api.http import app
from barsi.config import settings

client = TestClient(app)


def test_drip_service_simulation_and_allocation(tmp_path):
    """Valida o cálculo quantitativo de reinvestimento automático de dividendos (DRIP Barsi)."""
    db_file = str(tmp_path / "test_drip.db")
    repo = BudgetRepository(db_file)

    # Cadastra posições canônicas
    p1 = Position(
        ticker="BBAS3",
        row_index=1,
        empresa="Banco do Brasil",
        setor="Bancos",
        qtd_antes=500,
        qtd_aporte=0,
        preco_atual=25.0,
        div_proj=2.50,
        preco_teto=41.66,
        margem_seguranca=66.6,
        valor_total=12500.0
    )
    p2 = Position(
        ticker="TAEE11",
        row_index=2,
        empresa="Taesa",
        setor="Energia",
        qtd_antes=400,
        qtd_aporte=0,
        preco_atual=35.0,
        div_proj=3.50,
        preco_teto=58.33,
        margem_seguranca=66.6,
        valor_total=14000.0
    )
    p3 = Position(
        ticker="VALE3",
        row_index=3,
        empresa="Vale",
        setor="Mineração",
        qtd_antes=100,
        qtd_aporte=0,
        preco_atual=60.0,
        div_proj=2.00,
        preco_teto=33.33,
        margem_seguranca=-44.4,  # Acima do teto: NÃO deve ser elegível
        valor_total=6000.0
    )
    repo.save_positions([p1, p2, p3])

    # Registra dividendos recebidos no mês
    div = DividendReceived(
        ticker="BBAS3",
        data_pagamento="2026-11-10",
        mes_ano="2026-11",
        valor_total=750.0,
        quantidade=500
    )
    repo.record_dividend_received(div)

    drip_service = DripService(repo)

    # 1. Recuperação de dividendos disponíveis no mês
    disponivel = drip_service.get_available_dividends_for_month("2026-11")
    assert disponivel == 750.0

    # 2. Simulação com o valor dos dividendos recebidos
    sim = drip_service.simulate_drip(valor_disponivel=750.0, mes_ano="2026-11", balance_sectors=True)

    assert sim["valor_disponivel"] == 750.0
    assert sim["valor_alocado"] > 0.0
    assert sim["valor_alocado"] <= 750.0
    assert sim["troco_residual"] >= 0.0
    assert sim["valor_alocado"] + sim["troco_residual"] == pytest.approx(750.0, 0.01)
    assert sim["provento_anual_adicional"] > 0.0
    assert sim["yield_medio_adicional_pct"] >= 6.0  # Hurdle Barsi

    tickers_sugeridos = [o["ticker"] for o in sim["ordens"]]
    assert "VALE3" not in tickers_sugeridos  # VALE3 tinha margem negativa
    assert any(t in ("BBAS3", "TAEE11") for t in tickers_sugeridos)

    # 3. Aplicação das ordens sugeridas no SQLite canônico
    res_apply = drip_service.apply_drip_orders(sim["ordens"])
    assert res_apply["status"] == "ok"
    assert res_apply["aplicadas"] >= 1
    assert res_apply["total_reinvestido"] > 0

    # Verifica se as posições foram devidamente atualizadas no banco
    updated_positions = repo.load_positions()
    pos_map = {p.ticker: p for p in updated_positions}
    assert pos_map["BBAS3"].qtd_aporte > 0 or pos_map["TAEE11"].qtd_aporte > 0


def test_monthly_closing_service_and_html(tmp_path):
    """Valida consolidação de dados e geração de relatório HTML executivo e PDF."""
    db_file = str(tmp_path / "test_closing.db")
    repo = BudgetRepository(db_file)

    p1 = Position(
        ticker="BBAS3",
        row_index=1,
        empresa="Banco do Brasil",
        setor="Bancos",
        qtd_antes=1000,
        qtd_aporte=0,
        preco_atual=28.0,
        div_proj=2.80,
        preco_teto=46.66,
        margem_seguranca=66.6,
        valor_total=28000.0
    )
    repo.save_positions([p1])

    # Insere dividendos no mês
    div = DividendReceived(
        ticker="BBAS3",
        data_pagamento="2026-11-05",
        mes_ano="2026-11",
        valor_total=1200.0,
        quantidade=1000
    )
    repo.record_dividend_received(div)

    service = MonthlyClosingService(repo)

    # 1. Extração analítica dos dados de fechamento
    dados = service.get_closing_data("2026-11")
    assert dados["mes_ano"] == "2026-11"
    assert dados["patrimonio_total"] == 28000.0
    assert dados["total_divs_mes"] == 1200.0
    assert dados["total_cotas"] == 1000
    assert len(dados["radar_oportunidades"]) >= 1

    # 2. Renderização de HTML profissional para impressão A4
    html = service.generate_html_report("2026-11")
    assert "<!DOCTYPE html>" in html
    assert "Fechamento Mensal" in html
    assert "BBAS3" in html
    assert "2026-11" in html
    assert "Filosofia Luiz Barsi" in html

    # 3. Compilação para PDF via Playwright Chromium
    pdf_bytes = service.generate_pdf_report("2026-11")
    assert isinstance(pdf_bytes, bytes)
    assert len(pdf_bytes) > 5000
    assert pdf_bytes.startswith(b"%PDF-")  # Header válido padrão PDF


def test_api_drip_and_reports_endpoints(tmp_path, monkeypatch):
    """Valida as rotas HTTP e API do DRIP e do Fechamento Mensal em PDF."""
    db_file = str(tmp_path / "test_api_sprint2.db")
    monkeypatch.setattr(settings, "sqlite_db_path", db_file)

    repo = BudgetRepository(db_file)
    p = Position(
        ticker="TAEE11",
        row_index=1,
        empresa="Taesa",
        setor="Energia",
        qtd_antes=500,
        qtd_aporte=0,
        preco_atual=35.0,
        div_proj=3.50,
        preco_teto=58.33,
        margem_seguranca=66.6,
        valor_total=17500.0
    )
    repo.save_positions([p])

    user_repo = UserRepository(db_file)
    user_repo.create_user("investidor_sprint2", "senhaForte123", role="admin")
    u = user_repo.get_by_username("investidor_sprint2")

    session_token = create_session_token(
        user_id=u["id"],
        username=u["username"],
        role=u["role"],
        secret_key=settings.secret_key
    )
    cookies = {"barsi_session": session_token}

    # 1. Simulação DRIP via API
    resp_unauth = client.post("/api/drip/simulate", json={"valor_disponivel": 500.0})
    assert resp_unauth.status_code == 401

    resp_sim = client.post("/api/drip/simulate", json={"valor_disponivel": 500.0}, cookies=cookies)
    assert resp_sim.status_code == 200
    data_sim = resp_sim.json()
    assert data_sim["valor_disponivel"] == 500.0
    assert len(data_sim["ordens"]) >= 1

    # 2. Aplicação de ordens DRIP via API
    payload_apply = {
        "ordens": [
            {"ticker": "TAEE11", "qtd_sugerida": 10, "valor_gasto": 350.0}
        ]
    }
    resp_apply = client.post("/api/drip/apply", json=payload_apply, cookies=cookies)
    assert resp_apply.status_code == 200
    assert resp_apply.json()["status"] == "ok"
    assert resp_apply.json()["aplicadas"] == 1

    # 3. Prévia em HTML do Fechamento Mensal
    resp_preview = client.get("/api/reports/monthly-closing/preview?mes_ano=2026-11", cookies=cookies)
    assert resp_preview.status_code == 200
    assert "text/html" in resp_preview.headers["content-type"]
    assert "Fechamento Mensal" in resp_preview.text

    # 4. Download do PDF Executivo compilado
    resp_pdf = client.get("/api/reports/monthly-closing?mes_ano=2026-11", cookies=cookies)
    assert resp_pdf.status_code == 200
    assert resp_pdf.headers["content-type"] == "application/pdf"
    assert "attachment; filename=" in resp_pdf.headers["content-disposition"]
    assert resp_pdf.content.startswith(b"%PDF-")
