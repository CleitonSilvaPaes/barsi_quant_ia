"""Testes para a Web Suite Modular, Rotas com Sidebar e Endpoints da Onda 1."""
import pytest
from fastapi.testclient import TestClient
from barsi.api.http import app
from barsi.infra.security import create_session_token
from barsi.config import settings
from barsi.adapters.factory import AdapterFactory
from barsi.domain.models import Position, AcaoBarsi

class MockSheetsAdapter:
    def __init__(self):
        self.positions = [
            Position(
                ticker="BBAS3", row_index=10, empresa="Banco do Brasil", setor="Bancos",
                qtd_antes=37, qtd_aporte=18, qtd_total=55, preco_atual=17.13, div_proj=2.30,
                preco_teto=38.33, margem_seguranca=123.8, status_barsi=AcaoBarsi.ACUMULAR,
                feedback_usuario="CONCORDO"
            ),
            Position(
                ticker="ITUB4", row_index=11, empresa="Itaú Unibanco", setor="Bancos",
                qtd_antes=7, qtd_aporte=0, qtd_total=7, preco_atual=40.53, div_proj=2.40,
                preco_teto=40.00, margem_seguranca=-1.3, status_barsi=AcaoBarsi.AGUARDAR,
                feedback_usuario="DISCORDO"
            )
        ]

    async def load_positions(self):
        return self.positions

    async def update_positions(self, positions, invalidate=True):
        self.positions = positions

@pytest.fixture
def auth_client(monkeypatch, tmp_path):
    mock_sheets = MockSheetsAdapter()
    monkeypatch.setattr(AdapterFactory, "create_sheets_adapter", lambda cfg: mock_sheets)
    
    test_db = str(tmp_path / "test_suite.db")
    monkeypatch.setattr(settings, "sqlite_db_path", test_db)

    client = TestClient(app)
    token = create_session_token(user_id=1, username="admin_test", role="admin", secret_key=settings.secret_key)
    client.cookies.set("barsi_session", token)
    return client

def test_page_routes_render_html(auth_client):
    pages = ["/", "/carteira", "/aportes", "/budget", "/extratos", "/insights", "/analytics", "/settings", "/admin", "/help"]
    for p in pages:
        res = auth_client.get(p)
        assert res.status_code == 200, f"Falha na página {p}"
        assert "Barsi Quant IA" in res.text
        assert "sidebar" in res.text

def test_home_summary_api(auth_client):
    res = auth_client.get("/api/home/summary")
    assert res.status_code == 200
    data = res.json()
    assert "patrimonio_total" in data
    assert "cotas_totais" in data
    assert "aporte_programado" in data
    assert "top_insights" in data
    assert len(data["top_insights"]) >= 1

def test_insights_api_and_feedback(auth_client):
    res = auth_client.get("/api/insights")
    assert res.status_code == 200
    data = res.json()
    assert "taxa_concordancia" in data
    assert data["total_count"] == 2
    assert data["acumular_count"] == 1
    assert data["aguardar_count"] == 1

    # Testa envio de feedback 👍 Concordo
    fb_res = auth_client.post("/api/insights/feedback", json={"ticker": "BBAS3", "feedback": "CONCORDO"})
    assert fb_res.status_code == 200
    assert fb_res.json()["ok"] is True

def test_analytics_data_api(auth_client):
    res = auth_client.get("/api/analytics/data")
    assert res.status_code == 200
    data = res.json()
    assert "patrimonio_atual" in data
    assert "snapshots" in data
    assert "setores" in data
    assert "dividendos" in data
    assert "Bancos" in data["setores"]

