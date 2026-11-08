"""Testes para a Web Suite Modular, Rotas com Sidebar e Endpoints da Onda 1."""
import pytest
from fastapi.testclient import TestClient
from barsi.api.http import app
import barsi.api.http as http_module
from barsi.infra.security import create_session_token
from barsi.config import settings
from barsi.adapters.factory import AdapterFactory
from barsi.adapters.sheets.gspread_adapter import GSpreadAdapter
from barsi.adapters.history.budget_repository import BudgetRepository
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

    # Bug 4, T1 & Issue 5: Reseta caches globais para isolamento estrito contra produção
    http_module._user_repo_cache = None
    http_module._benchmarks_cache = None
    http_module._benchmarks_cache_time = 0.0
    GSpreadAdapter.invalidate_cache()

    from barsi.adapters.history.user_repository import UserRepository
    u_repo = UserRepository(test_db)
    u_repo.create_user("admin_test", "pass123", "admin")

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

def test_settings_test_telegram_endpoint(auth_client, monkeypatch):
    """Bug 1: Valida que o endpoint de teste do Telegram usa notify assíncrono e não falha com AttributeError."""
    class MockTelegramNotifier:
        async def notify(self, title: str, body: str, level: str = "info") -> bool:
            return True

    monkeypatch.setattr(AdapterFactory, "create_notification_adapter", lambda cfg: MockTelegramNotifier())
    res = auth_client.post("/api/settings/test/telegram")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert "Telegram" in data["detail"]

def test_analytics_benchmarks_endpoint(auth_client):
    """Bug 3: Valida que o endpoint de benchmarks retorna dados estruturados sem campos hardcoded fictícios."""
    res = auth_client.get("/api/analytics/benchmarks")
    assert res.status_code == 200
    data = res.json()
    assert "cdi_12m" in data
    assert "ibov_12m" in data
    assert "fonte" in data
    assert data["cdi_12m"] is not None

def test_build_real_portfolio_snapshots_with_price_history(tmp_path):
    """T3: Valida a construção da curva patrimonial real a partir de cotações históricas diárias no SQLite."""
    test_db = str(tmp_path / "snap_test.db")
    repo = BudgetRepository(test_db)
    
    pos = [
        Position(ticker="BBAS3", row_index=10, qtd_antes=100, preco_atual=17.0, div_proj=2.0, status_barsi=AcaoBarsi.ACUMULAR, setor="Bancos"),
        Position(ticker="SAPR4", row_index=11, qtd_antes=200, preco_atual=6.0, div_proj=0.5, status_barsi=AcaoBarsi.ACUMULAR, setor="Saneamento")
    ]
    repo.persist_positions(pos)
    
    dates_prices = [
        ("2026-07-31", 15.0, 5.0),
        ("2026-08-31", 16.0, 5.5),
        ("2026-09-30", 16.5, 5.8),
        ("2026-10-31", 17.0, 6.0)
    ]
    for dt, p_bbas, p_sapr in dates_prices:
        repo.save_price_history("BBAS3", dt, p_bbas)
        repo.save_price_history("SAPR4", dt, p_sapr)

    snapshots = repo.build_real_portfolio_snapshots(limit_months=12)
    assert len(snapshots) == 4
    # Snapshot inicial: 100*15.0 + 200*5.0 = 2500
    assert snapshots[0]["patrimonio_total"] == 2500.0
    assert snapshots[0]["data_snapshot"] == "2026-07-31"
    # Snapshot final: 100*17.0 + 200*6.0 = 2900
    assert snapshots[-1]["patrimonio_total"] == 2900.0
    assert snapshots[-1]["data_snapshot"] == "2026-10-31"

def test_admin_page_rbac_investidor_blocked(auth_client):
    """Valida que usuários com role 'investidor' são bloqueados de acessar /admin e o link não aparece na sidebar."""
    # 1. Usuário admin acessa normalmente
    admin_res = auth_client.get("/admin")
    assert admin_res.status_code == 200
    assert "Admin & Usuários" in admin_res.text

    # 2. Cria sessão com perfil 'investidor'
    investidor_token = create_session_token(user_id=2, username="investidor_joao", role="investidor", secret_key=settings.secret_key)
    investidor_client = TestClient(app)
    investidor_client.cookies.set("barsi_session", investidor_token)

    # Sidebar não deve exibir o link para /admin
    home_res = investidor_client.get("/")
    assert home_res.status_code == 200
    assert "Admin & Usuários" not in home_res.text

    # Tentativa de acesso direto a /admin deve ser bloqueada com redirecionamento
    blocked_res = investidor_client.get("/admin", follow_redirects=False)
    assert blocked_res.status_code == 303
    assert "error=Acesso+restrito" in blocked_res.headers.get("location", "")
