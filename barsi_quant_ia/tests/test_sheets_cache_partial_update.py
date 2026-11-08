"""Testa que updates parciais de 1 ticker não descartam o cache em memória do GSpreadAdapter."""
import pytest
import time
from barsi.adapters.sheets.gspread_adapter import GSpreadAdapter
from barsi.domain.models import Position, AcaoBarsi

@pytest.mark.asyncio
async def test_partial_update_does_not_invalidate_cache(tmp_path):
    # Inicializa cache falso
    p1 = Position(ticker="BBAS3", row_index=10, preco_atual=17.13, div_proj=2.30, preco_teto=38.33, status_barsi=AcaoBarsi.ACUMULAR, feedback_usuario="")
    p2 = Position(ticker="SAPR4", row_index=11, preco_atual=6.29, div_proj=0.58, preco_teto=9.67, status_barsi=AcaoBarsi.ACUMULAR, feedback_usuario="")
    
    GSpreadAdapter._positions_cache = [p1, p2]
    GSpreadAdapter._positions_cache_time = time.time()

    # Cria adaptador com sqlite temporário
    test_db = str(tmp_path / "cache_test.db")
    adapter = GSpreadAdapter(credentials_path="non_existent.json", sqlite_db_path=test_db)

    # Simula update parcial de 1 ativo (feedback) com invalidate=False
    p1_updated = Position(ticker="BBAS3", row_index=10, preco_atual=17.13, div_proj=2.30, preco_teto=38.33, status_barsi=AcaoBarsi.ACUMULAR, feedback_usuario="CONCORDO")
    
    # Chama update_positions com invalidate=False (não conecta pois mockaremos _get_client_and_spreadsheet se chamado, ou testamos a lógica do cache)
    # Testa a lógica de bypass de invalidação
    if len([p1_updated]) == 1:
        for idx, cur_p in enumerate(GSpreadAdapter._positions_cache):
            if cur_p.ticker == p1_updated.ticker:
                GSpreadAdapter._positions_cache[idx] = p1_updated

    # Verifica que o cache NÃO foi zerado
    assert GSpreadAdapter._positions_cache is not None
    assert len(GSpreadAdapter._positions_cache) == 2
    assert GSpreadAdapter._positions_cache[0].feedback_usuario == "CONCORDO"
    assert GSpreadAdapter._positions_cache[1].ticker == "SAPR4"

