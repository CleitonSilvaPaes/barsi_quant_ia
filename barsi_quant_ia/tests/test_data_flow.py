"""Testa o fluxo de dados desacoplado: SQLite como FONTE ÚNICA DA VERDADE e export sob demanda."""
import pytest
from fastapi.testclient import TestClient
from barsi.api.http import app
from barsi.adapters.factory import AdapterFactory
from barsi.adapters.history.budget_repository import BudgetRepository
from barsi.infra.security import create_session_token
from barsi.config import settings
from barsi.domain.models import Position, AcaoBarsi

class SpySheetsAdapter:
    """Rastreia chamadas para garantir que save não toca no Sheets e export toca."""
    def __init__(self):
        self.update_called = False
        self.positions = []

    async def load_positions(self):
        return self.positions

    async def update_positions(self, positions, invalidate=True):
        self.update_called = True
        self.positions = positions

def test_sqlite_is_primary_source_and_save_is_instant(tmp_path, monkeypatch):
    test_db = str(tmp_path / "dataflow_test.db")
    monkeypatch.setattr(settings, "sqlite_db_path", test_db)
    
    spy_sheets = SpySheetsAdapter()
    monkeypatch.setattr(AdapterFactory, "create_sheets_adapter", lambda cfg: spy_sheets)

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
            "feedback": "Modificado offline com sucesso"
        }]
    }

    # 1. Executa a edição da carteira
    res = client.post("/api/carteira", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["updated_count"] == 1

    # Valida que o Sheets NÃO foi acionado no save automático (desacoplamento total)
    assert spy_sheets.update_called is False

    # Valida que o SQLite FOI ATUALIZADO com sucesso imediato (Fonte da Verdade)
    positions_sqlite = repo.load_positions()
    bbas3 = next(p for p in positions_sqlite if p.ticker == "BBAS3")
    assert bbas3.qtd_antes == 37
    assert bbas3.qtd_aporte == 18
    assert bbas3.feedback_usuario == "Modificado offline com sucesso"

    # 2. Agora executa a exportação sob demanda para o Google Sheets
    export_res = client.post("/api/export/sheets")
    assert export_res.status_code == 200
    assert export_res.json()["ok"] is True
    # Valida que agora sim o Sheets foi atualizado com os dados consolidados do SQLite
    assert spy_sheets.update_called is True
    assert spy_sheets.positions[0].qtd_antes == 37


