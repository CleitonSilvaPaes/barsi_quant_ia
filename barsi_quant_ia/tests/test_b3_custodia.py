"""Testes de importação e conciliação de Custódia Oficial da B3."""
import pytest
from pathlib import Path
import openpyxl
from barsi.adapters.history.budget_repository import BudgetRepository
from barsi.services.b3_custodia_service import B3CustodiaService
from barsi.domain.models import Position, AcaoBarsi

def _create_mock_b3_excel(target_path: Path):
    """Cria uma planilha Excel em memória/disco idêntica à oficial da Área do Investidor da B3."""
    wb = openpyxl.Workbook()

    # Aba Acoes
    ws_acoes = wb.active
    ws_acoes.title = "Acoes"
    ws_acoes.append([
        "Produto", "Instituição", "Conta", "Código de Negociação", "CNPJ da Empresa",
        "Código ISIN / Distribuição", "Tipo", "Escriturador", "Quantidade",
        "Quantidade Disponível", "Quantidade Indisponível", "Motivo",
        "Preço de Fechamento", "Valor Atualizado"
    ])
    ws_acoes.append(["BBAS3 - BCO BRASIL S.A.", "NU INVESTIMENTOS", "6103892", "BBAS3", "00000000000191", "BRBBASACNOR3", "ON", "BB", 55, 55, "-", "-", 15.48, 851.40])
    ws_acoes.append(["BBSE3 - BB SEGURIDADE", "NU INVESTIMENTOS", "6103892", "BBSE3", "17344597000194", "BRBBSEACNOR5", "ON", "BB", 25, 25, "-", "-", 37.68, 942.00])
    ws_acoes.append(["SAPR4 - SANEPAR", "NU INVESTIMENTOS", "6103892", "SAPR4", "76484013000145", "BRSAPRACNPR6", "PN", "BRADESCO", 93, 93, "-", "-", 6.40, 595.20])
    ws_acoes.append(["", "", "", "", "", "", "", "", "", "", "", "", "", "Total"])
    ws_acoes.append(["", "", "", "", "", "", "", "", "", "", "", "", "", 2388.60])

    # Aba Fundo de Investimento
    ws_fii = wb.create_sheet("Fundo de Investimento")
    ws_fii.append([
        "Produto", "Instituição", "Conta", "Código de Negociação", "CNPJ do Fundo",
        "Código ISIN / Distribuição", "Tipo", "Administrador", "Quantidade",
        "Quantidade Disponível", "Quantidade Indisponível", "Motivo",
        "Preço de Fechamento", "Valor Atualizado"
    ])
    ws_fii.append(["MXRF11 - MAXI RENDA FDO INV IMOB", "NU INVESTIMENTOS", "6103892", "MXRF11", "97521225000125", "BRMXRFCTF008", "Cotas", "BTG", 345, 345, "-", "-", 8.70, 3001.50])
    ws_fii.append(["XPLG11 - XP LOG FDO INV IMOB", "NU INVESTIMENTOS", "6103892", "XPLG11", "26502794000185", "BRXPLGCTF002", "Cotas", "OLIVEIRA", 2, 2, "-", "-", 88.50, 177.00])
    ws_fii.append(["", "", "", "", "", "", "", "", "", "", "", "", "", "Total"])
    ws_fii.append(["", "", "", "", "", "", "", "", "", "", "", "", "", 3178.50])

    wb.save(target_path)


def test_b3_custodia_parse_and_sync(tmp_path):
    """Valida a leitura e persistência da custódia B3 de forma autocontida e isolada."""
    db_file = str(tmp_path / "test_b3.db")
    repo = BudgetRepository(db_file)
    service = B3CustodiaService(repo)

    mock_xlsx = tmp_path / "custodia_b3.xlsx"
    _create_mock_b3_excel(mock_xlsx)
    assert mock_xlsx.exists()

    # 1. Parsing da planilha
    ativos = service.parse_b3_excel(mock_xlsx)
    assert len(ativos) == 5
    tickers = {a["ticker"] for a in ativos}
    assert "BBAS3" in tickers
    assert "MXRF11" in tickers
    assert "SAPR4" in tickers
    assert "XPLG11" in tickers

    # 2. Sincronização inicial no SQLite
    res = service.sync_custodia_to_sqlite(mock_xlsx)
    assert res["status"] == "ok"
    assert res["total_importados"] == 5
    assert res["novos"] == 5
    assert res["patrimonio_total"] == round(851.40 + 942.00 + 595.20 + 3001.50 + 177.00, 2)

    # 3. Validação das posições salvas
    pos = repo.load_positions()
    assert len(pos) == 5
    pos_map = {p.ticker: p for p in pos}
    assert pos_map["BBAS3"].qtd_antes == 55
    assert pos_map["MXRF11"].qtd_antes == 345
    assert pos_map["SAPR4"].qtd_antes == 93

    # 4. Idempotência e preservação de dados
    res2 = service.sync_custodia_to_sqlite(mock_xlsx)
    assert res2["novos"] == 0
    assert res2["atualizados"] == 5
    assert res2["total_importados"] == 5

    # 5. Se houver planilha física na raiz local (desenvolvimento), valida também
    local_real = Path("posicao-2026-11-14-01-02-22.xlsx")
    if local_real.exists():
        res_real = service.sync_custodia_to_sqlite(local_real)
        assert res_real["status"] == "ok"
        assert res_real["total_importados"] == 10
