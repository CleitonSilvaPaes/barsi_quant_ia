"""Testa que updates parciais de 1 ticker não descartam o cache em memória do GSpreadAdapter."""
import pytest
import time
from barsi.adapters.sheets.gspread_adapter import GSpreadAdapter
from barsi.domain.models import Position, AcaoBarsi

@pytest.mark.asyncio
async def test_partial_update_does_not_invalidate_cache(tmp_path, monkeypatch):
    p1 = Position(
        ticker="BBAS3", row_index=10, preco_atual=17.13, div_proj=2.30,
        preco_teto=38.33, status_barsi=AcaoBarsi.ACUMULAR, feedback_usuario=""
    )
    p2 = Position(
        ticker="SAPR4", row_index=11, preco_atual=6.29, div_proj=0.58,
        preco_teto=9.67, status_barsi=AcaoBarsi.ACUMULAR, feedback_usuario=""
    )
    
    GSpreadAdapter._positions_cache = [p1, p2]
    GSpreadAdapter._positions_cache_time = time.time()

    # Mock completo do cliente GSpread
    class FakeWS:
        def batch_update(self, updates):
            pass
        def update(self, **kw):
            pass

    class FakeSpreadsheet:
        def worksheets(self):
            return [type("W", (), {"title": "Screener_BEST_Custodia"})()]
        def worksheet(self, name):
            return FakeWS()

    test_db = str(tmp_path / "cache_test.db")
    adapter = GSpreadAdapter(credentials_path="mock_creds.json", sqlite_db_path=test_db)
    monkeypatch.setattr(adapter, "_get_client_and_spreadsheet", lambda: FakeSpreadsheet())

    # Executa a chamada real a update_positions com invalidate=False
    p1_updated = p1.model_copy(update={"feedback_usuario": "CONCORDO"})
    await adapter.update_positions([p1_updated], invalidate=False)

    # Verifica que o cache NÃO foi zerado e que a posição foi atualizada em memória
    assert GSpreadAdapter._positions_cache is not None
    assert len(GSpreadAdapter._positions_cache) == 2
    assert GSpreadAdapter._positions_cache[0].ticker == "BBAS3"
    assert GSpreadAdapter._positions_cache[0].feedback_usuario == "CONCORDO"
    assert GSpreadAdapter._positions_cache[1].ticker == "SAPR4"
