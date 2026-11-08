"""Testa o seeding dinâmico de snapshots patrimoniais para o gráfico de evolução histórica."""
import pytest
from datetime import datetime
from fastapi.testclient import TestClient
from barsi.api.http import app
from barsi.adapters.factory import AdapterFactory
from barsi.adapters.history.budget_repository import BudgetRepository
from barsi.infra.security import create_session_token
from barsi.config import settings
from barsi.domain.models import Position, AcaoBarsi

class MockSheetsForSeeding:
    async def load_positions(self):
        return [
            Position(ticker="BBAS3", row_index=10, setor="Bancos", qtd_antes=37, preco_atual=17.13, div_proj=2.30, status_barsi=AcaoBarsi.ACUMULAR),
            Position(ticker="MXRF11", row_index=11, setor="FII - Papel", qtd_antes=310, preco_atual=8.80, div_proj=1.08, status_barsi=AcaoBarsi.ACUMULAR)
        ]

def test_analytics_returns_at_least_six_snapshots_with_dynamic_dates(tmp_path, monkeypatch):
    test_db = str(tmp_path / "seeding_test.db")
    monkeypatch.setattr(settings, "sqlite_db_path", test_db)
    monkeypatch.setattr(AdapterFactory, "create_sheets_adapter", lambda cfg: MockSheetsForSeeding())

    client = TestClient(app)
    token = create_session_token(user_id=1, username="admin_seed", role="admin", secret_key=settings.secret_key)
    client.cookies.set("barsi_session", token)

    res = client.get("/api/analytics/data")
    assert res.status_code == 200
    data = res.json()

    snapshots = data["snapshots"]
    # Valida que há pelo menos 6 pontos históricos para o gráfico
    assert len(snapshots) >= 6

    # Valida que o snapshot mais recente corresponde ao ano corrente (sem datas hardcoded antigas)
    current_year = str(datetime.now().year)
    recent_snapshot = snapshots[-1]
    assert recent_snapshot["data_snapshot"].startswith(current_year)
    assert recent_snapshot["patrimonio_total"] > 0
    assert "setores" in recent_snapshot
    assert isinstance(recent_snapshot["setores"], dict)

