"""Testa o endpoint de feedback em lote (Batch) e a consolidação em 1 chamada ao Sheets."""
import pytest
from fastapi.testclient import TestClient
from barsi.api.http import app
from barsi.adapters.factory import AdapterFactory
from barsi.adapters.history.budget_repository import BudgetRepository
from barsi.infra.security import create_session_token
from barsi.config import settings
from barsi.domain.models import Position, AcaoBarsi

class SpySheetsAdapter:
    def __init__(self):
        self.update_call_count = 0
        self.updated_positions = []
        self.positions = [
            Position(ticker=f"TICK{i}", row_index=10+i, preco_atual=10.0, div_proj=1.0, status_barsi=AcaoBarsi.ACUMULAR)
            for i in range(1, 6)
        ]

    async def load_positions(self):
        return self.positions

    async def update_positions(self, positions, invalidate=True):
        self.update_call_count += 1
        self.updated_positions = positions

def test_insights_batch_feedback_calls_sheets_once(tmp_path, monkeypatch):
    test_db = str(tmp_path / "batch_test.db")
    monkeypatch.setattr(settings, "sqlite_db_path", test_db)
    
    spy_sheets = SpySheetsAdapter()
    monkeypatch.setattr(AdapterFactory, "create_sheets_adapter", lambda cfg: spy_sheets)

    # Popula SQLite
    repo = BudgetRepository(test_db)
    repo.save_positions(spy_sheets.positions)

    client = TestClient(app)
    token = create_session_token(user_id=1, username="admin_batch", role="admin", secret_key=settings.secret_key)
    client.cookies.set("barsi_session", token)

    payload = {
        "items": [
            {"ticker": "TICK1", "feedback": "CONCORDO"},
            {"ticker": "TICK2", "feedback": "DISCORDO"},
            {"ticker": "TICK3", "feedback": "CONCORDO"},
            {"ticker": "TICK4", "feedback": "CONCORDO"},
            {"ticker": "TICK5", "feedback": "DISCORDO"},
        ]
    }

    res = client.post("/api/insights/feedback/batch", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["count"] == 5
    assert data["synced_to_sheets"] is True

    # Valida que o Sheets recebeu EXATAMENTE 1 chamada em batch (e não 5 chamadas individuais!)
    assert spy_sheets.update_call_count == 1
    assert len(spy_sheets.updated_positions) == 5

    # Valida que todos os 5 foram atualizados no SQLite
    sqlite_positions = {p.ticker: p for p in repo.load_positions()}
    for i in range(1, 6):
        expected_fb = "CONCORDO" if i in [1, 3, 4] else "DISCORDO"
        assert sqlite_positions[f"TICK{i}"].feedback_usuario == expected_fb

