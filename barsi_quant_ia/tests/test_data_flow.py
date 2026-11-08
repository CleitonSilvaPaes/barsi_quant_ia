"""Testa o fluxo de dados rigoroso: SQLite como fonte da verdade e Outbox Pattern quando Sheets falha."""
import pytest
from fastapi.testclient import TestClient
from barsi.api.http import app
from barsi.adapters.factory import AdapterFactory
from barsi.adapters.history.budget_repository import BudgetRepository
from barsi.infra.security import create_session_token
from barsi.config import settings
from barsi.domain.models import Position, AcaoBarsi

class FailingSheetsAdapter:
    """Simula falha de conectividade / Rate Limit 429 no Google Sheets."""
    async def load_positions(self):
        return [
            Position(ticker="BBAS3", row_index=10, qtd_antes=10, qtd_aporte=5, preco_atual=17.13, div_proj=2.30, status_barsi=AcaoBarsi.ACUMULAR)
        ]

    async def update_positions(self, positions, invalidate=True):
        raise ConnectionError("Google Sheets API 429 Quota Exceeded (Simulado)")

def test_sqlite_updated_even_if_sheets_fails(tmp_path, monkeypatch):
    test_db = str(tmp_path / "dataflow_test.db")
    monkeypatch.setattr(settings, "sqlite_db_path", test_db)
    monkeypatch.setattr(AdapterFactory, "create_sheets_adapter", lambda cfg: FailingSheetsAdapter())

    # Prepara posição inicial no SQLite
    repo = BudgetRepository(test_db)
    repo.save_positions([
        Position(ticker="BBAS3", row_index=10, qtd_antes=10, qtd_aporte=5, preco_atual=17.13, div_proj=2.30, status_barsi=AcaoBarsi.ACUMULAR)
    ])

    client = TestClient(app)
    token = create_session_token(user_id=1, username="admin_flow", role="admin", secret_key=settings.secret_key)
    client.cookies.set("barsi_session", token)

    payload = {
        "items": [{
            "ticker": "BBAS3",
            "row_index": 10,
            "div_proj": 2.30,
            "qtd_antes": 37,
            "qtd_aporte": 18,
            "feedback": "Modificado offline"
        }]
    }

    # Executa a edição da carteira
    res = client.post("/api/carteira", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["synced_to_sheets"] is False  # Sheets falhou, mas endpoint respondeu com sucesso!

    # 1. Valida que o SQLite FOI ATUALIZADO com sucesso imediato (Fonte da Verdade)
    positions_sqlite = repo.load_positions()
    bbas3 = next(p for p in positions_sqlite if p.ticker == "BBAS3")
    assert bbas3.qtd_antes == 37
    assert bbas3.qtd_aporte == 18
    assert bbas3.feedback_usuario == "Modificado offline"

    # 2. Valida que a gravação pendente foi salva na tabela Outbox (pending_sheets_writes)
    pending = repo.get_pending_sheets_writes()
    assert len(pending) == 1
    assert pending[0]["ticker"] == "BBAS3"
    assert pending[0]["payload"]["qtd_antes"] == 37

