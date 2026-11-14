"""Testes de importação e conciliação de Custódia Oficial da B3."""
import pytest
from pathlib import Path
from barsi.adapters.history.budget_repository import BudgetRepository
from barsi.services.b3_custodia_service import B3CustodiaService
from barsi.domain.models import Position, AcaoBarsi

def test_b3_custodia_parse_and_sync(tmp_path):
    """Valida a leitura e persistência da custódia B3 em banco temporário isolado."""
    db_file = str(tmp_path / "test_b3.db")
    repo = BudgetRepository(db_file)
    service = B3CustodiaService(repo)

    xlsx_path = Path("posicao-2026-11-14-01-02-22.xlsx")
    assert xlsx_path.exists(), "Planilha de teste B3 deve existir na raiz do projeto"

    # 1. Parsing da planilha
    ativos = service.parse_b3_excel(xlsx_path)
    assert len(ativos) == 10
    tickers = {a["ticker"] for a in ativos}
    assert "BBAS3" in tickers
    assert "MXRF11" in tickers
    assert "SAPR4" in tickers
    assert "XPLG11" in tickers

    # 2. Sincronização e criação inicial
    res = service.sync_custodia_to_sqlite(xlsx_path)
    assert res["status"] == "ok"
    assert res["total_importados"] == 10
    assert res["novos"] == 10
    assert res["patrimonio_total"] == 9056.79

    # 3. Validação das posições salvas no SQLite
    pos = repo.load_positions()
    assert len(pos) == 10
    pos_map = {p.ticker: p for p in pos}
    assert pos_map["BBAS3"].qtd_antes == 55
    assert pos_map["MXRF11"].qtd_antes == 345
    assert pos_map["SAPR4"].qtd_antes == 93

    # 4. Idempotência e atualização
    res2 = service.sync_custodia_to_sqlite(xlsx_path)
    assert res2["novos"] == 0
    assert res2["atualizados"] == 10
    assert res2["total_importados"] == 10

