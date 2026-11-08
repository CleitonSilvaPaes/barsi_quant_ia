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

def test_analytics_benchmarks_endpoint(auth_client, monkeypatch):
    """Bug 3 & Anti-flaky: Valida cálculo do CDI via mock da API do BCB sem dependência de rede externa."""
    import json
    fake_bcb_data = [
        {"data": f"01/{i:02d}/2025", "valor": "0.95"} for i in range(1, 13)
    ]
    class FakeBcbResponse:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): return json.dumps(fake_bcb_data).encode("utf-8")

    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: FakeBcbResponse())

    res = auth_client.get("/api/analytics/benchmarks")
    assert res.status_code == 200
    data = res.json()
    assert "cdi_12m" in data
    assert "ibov_12m" in data
    assert "fonte" in data
    assert data["cdi_12m"] is not None
    # 1.0095^12 - 1 = ~12.01%
    assert 11.5 <= data["cdi_12m"] <= 12.5

def test_analytics_benchmarks_offline_fallback(auth_client, monkeypatch):
    """Valida resiliência quando a API do BCB falha: cdi_12m assume None sem quebrar a rota."""
    def fake_failing_urlopen(*args, **kwargs):
        raise ConnectionResetError("Conexão com BCB recusada ou sem rede")

    monkeypatch.setattr("urllib.request.urlopen", fake_failing_urlopen)

    res = auth_client.get("/api/analytics/benchmarks")
    assert res.status_code == 200
    data = res.json()
    assert data["cdi_12m"] is None
    assert "spread_cdi" in data
    assert data["spread_cdi"] is None

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

def test_page_home_shows_error_toast_when_query_param(auth_client):
    """Issue A & B: Valida injeção segura de toast via json.dumps inclusive com aspas e apóstrofos."""
    res = auth_client.get("/?error=Acesso+restrito+a+administradores+d'água")
    assert res.status_code == 200
    assert 'showToast("Acesso restrito a administradores d\'água", \'error\')' in res.text
    assert "document.addEventListener('DOMContentLoaded'" in res.text

def test_page_home_no_toast_without_error(auth_client):
    """Issue B: Valida que a home não injeta script toast sem parâmetro de erro."""
    res = auth_client.get("/")
    assert res.status_code == 200
    assert "document.addEventListener('DOMContentLoaded'" not in res.text

def test_backup_download_rbac_admin_only(auth_client, tmp_path):
    """Issue C: Valida que baixar o backup SQLite é restrito exclusivamente ao perfil admin."""
    # 1. Usuário admin consegue baixar o banco
    res_admin = auth_client.get("/api/backup/download")
    assert res_admin.status_code == 200
    assert res_admin.headers["content-type"] == "application/octet-stream"

    # 2. Usuário leitor recebe 403 Forbidden
    leitor_token = create_session_token(user_id=3, username="leitor_maria", role="leitor", secret_key=settings.secret_key)
    leitor_client = TestClient(app)
    leitor_client.cookies.set("barsi_session", leitor_token)
    res_leitor = leitor_client.get("/api/backup/download")
    assert res_leitor.status_code == 403
    assert "não possui permissão" in res_leitor.json()["detail"]

    # 3. Usuário investidor também recebe 403 Forbidden
    inv_token = create_session_token(user_id=2, username="inv_joao", role="investidor", secret_key=settings.secret_key)
    inv_client = TestClient(app)
    inv_client.cookies.set("barsi_session", inv_token)
    res_inv = inv_client.get("/api/backup/download")
    assert res_inv.status_code == 403
    assert "não possui permissão" in res_inv.json()["detail"]

def test_export_sheets_endpoint(auth_client):
    """Caminho B: Valida exportação sob demanda do SQLite para o Google Sheets."""
    repo = BudgetRepository(settings.sqlite_db_path)
    repo.save_positions([
        Position(ticker="BBAS3", row_index=10, preco_atual=17.13, div_proj=2.30, status_barsi=AcaoBarsi.ACUMULAR)
    ])
    res = auth_client.post("/api/export/sheets")
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert "exportados com sucesso" in data["message"]

def test_export_sheets_rbac_leitor_blocked(auth_client):
    """Caminho B: Leitor não tem permissão para exportar/sobrescrever o Sheets."""
    leitor_token = create_session_token(user_id=3, username="leitor_export", role="leitor", secret_key=settings.secret_key)
    leitor_client = TestClient(app)
    leitor_client.cookies.set("barsi_session", leitor_token)
    res = leitor_client.post("/api/export/sheets")
    assert res.status_code == 403
    assert "não possui permissão" in res.json()["detail"]

def test_export_xlsx_endpoint(auth_client, tmp_path, monkeypatch):
    """Caminho B: Valida exportação sob demanda em formato Excel (.xlsx), criando arquivo limpo se não existir."""
    import os
    import openpyxl
    repo = BudgetRepository(settings.sqlite_db_path)
    repo.save_positions([
        Position(ticker="BBAS3", empresa="Banco do Brasil", setor="Bancos", row_index=10, preco_atual=17.13, div_proj=2.30, status_barsi=AcaoBarsi.ACUMULAR)
    ])

    # Aponta para um arquivo inexistente no tmp_path para validar a criação automática
    fresh_xlsx = str(tmp_path / "fresh_export.xlsx")
    assert not os.path.exists(fresh_xlsx)

    monkeypatch.setattr(settings, "local_xlsx_path", fresh_xlsx)

    res = auth_client.get("/api/export/xlsx")
    assert res.status_code == 200
    assert "spreadsheetml" in res.headers["content-type"]
    assert os.path.exists(fresh_xlsx)

    # Valida estrutura do arquivo Excel gerado
    wb = openpyxl.load_workbook(fresh_xlsx)
    assert "Screener_BEST_Custodia" in wb.sheetnames
    ws = wb["Screener_BEST_Custodia"]
    assert ws["A10"].value == "BBAS3"
    assert ws["D10"].value == 0
    assert ws["E10"].value == 0
    assert ws["H10"].value == 17.13
    assert ws["I10"].value == 2.30
    assert ws["P10"].value == "AGUARDAR"
    assert ws["Q10"].value == "NÃO"
    wb.close()

def test_export_xlsx_empty_db_returns_400(auth_client, tmp_path, monkeypatch):
    """Caminho B: Se o banco SQLite não tiver posições, retorna erro 400 amigável."""
    monkeypatch.setattr(BudgetRepository, "load_positions", lambda self: [])
    fresh_xlsx = str(tmp_path / "never_created.xlsx")
    monkeypatch.setattr(settings, "local_xlsx_path", fresh_xlsx)

    res = auth_client.get("/api/export/xlsx")
    assert res.status_code == 400
    assert "Nenhuma posição no SQLite para exportar" in res.json()["detail"]


