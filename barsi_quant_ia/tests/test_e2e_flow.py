"""Teste de Integração Ponta a Ponta (E2E) - Validação Completa do Fluxo Caminho B.

Fluxo testado:
1. Autenticação (Login) via formulário web e emissão de sessão (cookie JWT)
2. Acesso à página da Carteira (/carteira)
3. Edição inline e persistência instantânea de posições no SQLite (/api/carteira)
4. Exportação sob demanda para Excel (.xlsx) e validação da estrutura binária gerada
5. Exportação sob demanda para Google Sheets (/api/export/sheets)
6. Acesso aos Insights e envio de feedback em lote (/api/insights/feedback/batch)
7. Consulta à API de Carteira (/api/carteira) e Analytics (/api/analytics/data)
"""
import os
import openpyxl
from fastapi.testclient import TestClient
from barsi.config import settings
from barsi.api.http import app, get_user_repo
from barsi.adapters.history.user_repository import UserRepository
from barsi.adapters.history.budget_repository import BudgetRepository
from barsi.domain.models import Position, AcaoBarsi

def test_full_e2e_workflow(tmp_path, monkeypatch):
    # 1. Configura ambiente limpo e isolado em tmp_path
    test_db = str(tmp_path / "e2e_barsi.db")
    test_xlsx = str(tmp_path / "e2e_carteira.xlsx")

    monkeypatch.setattr(settings, "sqlite_db_path", test_db)
    monkeypatch.setattr(settings, "local_xlsx_path", test_xlsx)
    monkeypatch.setattr("barsi.api.http._user_repo_cache", None)

    # Inicializa admin de teste
    user_repo = UserRepository(test_db)
    user_repo.init_admin_if_empty("admin_e2e", "SenhaSuperSegura123!")

    # Seed com posições e cotações de mercado reais no SQLite (garante isolamento 100% de CI)
    budget_repo = BudgetRepository(test_db)
    budget_repo.save_positions([
        Position(
            ticker="BBAS3", empresa="Banco do Brasil S.A.", setor="Bancos",
            row_index=10, preco_atual=17.13, div_proj=2.30, status_barsi=AcaoBarsi.ACUMULAR
        ),
        Position(
            ticker="MXRF11", empresa="Maxi Renda FII", setor="FII - Papel",
            row_index=11, preco_atual=8.80, div_proj=1.08, status_barsi=AcaoBarsi.ACUMULAR
        ),
    ])

    client = TestClient(app)

    # 2. Login via POST /login
    login_resp = client.post(
        "/login",
        data={"username": "admin_e2e", "password": "SenhaSuperSegura123!"},
        follow_redirects=False
    )
    assert login_resp.status_code == 303
    assert "barsi_session" in client.cookies

    # 3. Acesso à Carteira (/carteira)
    carteira_resp = client.get("/carteira")
    assert carteira_resp.status_code == 200
    assert "Carteira" in carteira_resp.text

    # 4. Edição de posições e persistência imediata no SQLite via /api/carteira
    save_payload = {
        "items": [
            {
                "ticker": "BBAS3",
                "row_index": 10,
                "preco_atual": 17.13,
                "div_proj": 2.50,
                "qtd_antes": 100,
                "qtd_aporte": 25,
                "preco_teto": 28.00,
                "teto_manual": True,
                "feedback": "Banco sólido, valuation atrativo e dividendos crescentes"
            },
            {
                "ticker": "MXRF11",
                "row_index": 11,
                "preco_atual": 8.80,
                "div_proj": 1.15,
                "qtd_antes": 400,
                "qtd_aporte": 50,
                "preco_teto": 10.80,
                "teto_manual": False,
                "feedback": "FII de papel de alta liquidez"
            }
        ]
    }
    save_resp = client.post("/api/carteira", json=save_payload)
    assert save_resp.status_code == 200
    assert save_resp.json()["status"] == "ok"
    assert save_resp.json()["updated_count"] == 2

    # Confere se o SQLite refletiu os dados com integridade
    budget_repo = BudgetRepository(test_db)
    positions = budget_repo.load_positions()
    assert len(positions) >= 2
    
    pos_map = {p.ticker: p for p in positions}
    bbas = pos_map["BBAS3"]
    assert bbas.qtd_antes == 100
    assert bbas.qtd_aporte == 25
    assert bbas.qtd_total == 125
    assert bbas.div_proj == 2.50
    assert bbas.teto_manual is True
    assert "Banco sólido" in bbas.feedback_usuario

    mxrf = pos_map["MXRF11"]
    assert mxrf.qtd_antes == 400
    assert mxrf.qtd_aporte == 50
    assert mxrf.qtd_total == 450

    # 5. Exportação sob demanda para Excel (.xlsx)
    assert not os.path.exists(test_xlsx)
    export_xlsx_resp = client.get("/api/export/xlsx")
    assert export_xlsx_resp.status_code == 200
    assert "spreadsheetml" in export_xlsx_resp.headers["content-type"]
    assert os.path.exists(test_xlsx)

    # Inspeciona o arquivo Excel criado
    wb = openpyxl.load_workbook(test_xlsx)
    assert "Screener_BEST_Custodia" in wb.sheetnames
    ws = wb["Screener_BEST_Custodia"]
    assert ws["A10"].value == "BBAS3"
    assert ws["D10"].value == 100  # qtd_antes
    assert ws["E10"].value == 25   # qtd_aporte
    assert ws["F10"].value == 125  # qtd_total
    assert ws["I10"].value == 2.50 # div_proj
    assert ws["Q10"].value == "SIM"# teto_manual

    assert ws["A11"].value == "MXRF11"
    assert ws["D11"].value == 400
    assert ws["E11"].value == 50
    wb.close()

    # 6. Exportação sob demanda para Google Sheets (/api/export/sheets)
    captured_sheets_positions = []
    class MockSheetsAdapter:
        async def update_positions(self, pos_list, invalidate=True):
            captured_sheets_positions.extend(pos_list)

    monkeypatch.setattr("barsi.adapters.factory.AdapterFactory.create_sheets_adapter", lambda s: MockSheetsAdapter())
    export_sheets_resp = client.post("/api/export/sheets")
    assert export_sheets_resp.status_code == 200
    assert export_sheets_resp.json()["ok"] is True
    assert export_sheets_resp.json()["count"] >= 2
    assert len(captured_sheets_positions) >= 2
    tickers_exported = [p.ticker for p in captured_sheets_positions]
    assert "BBAS3" in tickers_exported
    assert "MXRF11" in tickers_exported

    # 7. Acesso à página de Insights (/insights) e Envio de Feedback em Lote
    insights_page_resp = client.get("/insights")
    assert insights_page_resp.status_code == 200
    assert "Insights" in insights_page_resp.text

    feedback_payload = {
        "items": [
            {"ticker": "BBAS3", "feedback": "Recomendação aprovada pela estratégia de proventos"},
            {"ticker": "MXRF11", "feedback": "Monitorar desdobramento de cotas"}
        ]
    }
    feedback_resp = client.post("/api/insights/feedback/batch", json=feedback_payload)
    assert feedback_resp.status_code == 200
    assert feedback_resp.json()["ok"] is True
    assert feedback_resp.json()["count"] == 2

    # Verifica se os novos feedbacks foram gravados no banco
    updated_pos = {p.ticker: p for p in budget_repo.load_positions()}
    assert "estratégia de proventos" in updated_pos["BBAS3"].feedback_usuario
    assert "desdobramento" in updated_pos["MXRF11"].feedback_usuario

    # 8. Consulta a API de Carteira (/api/carteira) e Analytics (/api/analytics/data)
    carteira_data_resp = client.get("/api/carteira")
    assert carteira_data_resp.status_code == 200
    carteira_items = carteira_data_resp.json()
    assert len(carteira_items) >= 2
    tickers_in_carteira = [p["ticker"] for p in carteira_items]
    assert "BBAS3" in tickers_in_carteira
    assert "MXRF11" in tickers_in_carteira

    analytics_resp = client.get("/api/analytics/data")
    assert analytics_resp.status_code == 200
    analytics_data = analytics_resp.json()
    assert analytics_data["patrimonio_atual"] > 0
    assert analytics_data["proventos_proj_ano"] > 0
    assert "setores" in analytics_data
    assert "Bancos" in analytics_data["setores"]
    assert "snapshots" in analytics_data
    assert len(analytics_data["snapshots"]) >= 3
