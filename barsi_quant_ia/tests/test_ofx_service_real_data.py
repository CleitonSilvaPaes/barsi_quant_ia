"""Testes de ponta a ponta com cenários reais para o parser OFX."""
import tempfile
import os
import pytest
from barsi.services.ofx_service import OFXParserService
from barsi.domain.models import Categoria

OFX_TEMPLATE = """OFXHEADER:100
<OFX>
<BANKTRANLIST>
{transactions}
</BANKTRANLIST>
</OFX>"""

def _make_stmttrn(trntype: str, dt: str, amt: float, fitid: str, memo: str) -> str:
    return f"""<STMTTRN>
<TRNTYPE>{trntype}</TRNTYPE>
<DTPOSTED>{dt}</DTPOSTED>
<TRNAMT>{amt:.2f}</TRNAMT>
<FITID>{fitid}</FITID>
<MEMO>{memo}</MEMO>
</STMTTRN>"""


def test_salario_nao_inclui_transferencia_de_familiar():
    """Garante que transferência de familiar não infla salário."""
    tx1 = _make_stmttrn("CREDIT", "20261105000000[-3:BRT]", 4340.50, "sal-01", "TED Recebida - SALÁRIO EMPRESA TITULAR TESTE")
    tx2 = _make_stmttrn("CREDIT", "20261106000000[-3:BRT]", 400.00, "pix-fam-01", "Transferência Pix - FULANA FAMILIAR")
    content = OFX_TEMPLATE.format(transactions=f"{tx1}\n{tx2}")

    with tempfile.TemporaryDirectory() as tmpdir:
        fpath = os.path.join(tmpdir, "extrato.ofx")
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(content)

        resumos = OFXParserService.processar_diretorio_extratos(
            tmpdir,
            titular_nomes=["TITULAR TESTE"],
            familiar_keywords=["FULANA FAMILIAR", "FAMILIAR"],
        )
        r = resumos["2026-11"]
        assert r.salario_entradas == 4340.50
        assert r.outras_entradas == 400.00
        assert r.total_entradas == 4740.50


def test_nizielma_credito_vai_para_outras_entradas():
    """Garante que créditos de familiar vão para outras_entradas e não para gastos de moradia/família."""
    tx_credito = _make_stmttrn("CREDIT", "20261110000000[-3:BRT]", 400.00, "fam-c-01", "Pix recebido de FAMILIAR")
    tx_debito = _make_stmttrn("DEBIT", "20261111000000[-3:BRT]", -250.00, "fam-d-01", "Pix enviado para FAMILIAR")
    content = OFX_TEMPLATE.format(transactions=f"{tx_credito}\n{tx_debito}")

    with tempfile.TemporaryDirectory() as tmpdir:
        fpath = os.path.join(tmpdir, "extrato.ofx")
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(content)

        resumos = OFXParserService.processar_diretorio_extratos(
            tmpdir,
            familiar_keywords=["FAMILIAR"],
        )
        r = resumos["2026-11"]
        # Crédito não pode entrar em moradia_e_familia (apenas débitos entram em despesas)
        assert r.moradia_e_familia == 250.00
        assert r.outras_entradas == 400.00


def test_rdb_resgate_vai_para_outras_entradas():
    """Garante que resgate de RDB entra como receita em outras_entradas e deduz da reserva líquida."""
    tx_aplic = _make_stmttrn("DEBIT", "20261102000000[-3:BRT]", -1200.00, "rdb-ap-01", "Aplicação RDB Resgate Diário")
    tx_resg = _make_stmttrn("CREDIT", "20261115000000[-3:BRT]", 500.00, "rdb-re-01", "Resgate RDB Resgate Diário")
    content = OFX_TEMPLATE.format(transactions=f"{tx_aplic}\n{tx_resg}")

    with tempfile.TemporaryDirectory() as tmpdir:
        fpath = os.path.join(tmpdir, "extrato.ofx")
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(content)

        resumos = OFXParserService.processar_diretorio_extratos(tmpdir)
        r = resumos["2026-11"]
        assert r.outras_entradas == 500.00
        assert r.reserva_rdb == 700.00  # 1200 aplic - 500 resg = 700 liquido aportado em reserva


def test_fatura_multipla_conta_so_o_maior():
    """Garante que múltiplos pagamentos de fatura separam a maior como fatura e os demais como encargos/avulsos."""
    tx_fat1 = _make_stmttrn("DEBIT", "20261108000000[-3:BRT]", -1250.00, "fat-01", "Pagamento de fatura do cartão de crédito")
    tx_fat2 = _make_stmttrn("DEBIT", "20261112000000[-3:BRT]", -150.00, "fat-02", "Pagamento de fatura do cartão de crédito")
    content = OFX_TEMPLATE.format(transactions=f"{tx_fat1}\n{tx_fat2}")

    with tempfile.TemporaryDirectory() as tmpdir:
        fpath = os.path.join(tmpdir, "extrato.ofx")
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(content)

        resumos = OFXParserService.processar_diretorio_extratos(tmpdir)
        r = resumos["2026-11"]
        assert r.faturas_cartao == 1250.00
        assert r.encargos_cartao == 150.00
        assert r.faturas_e_boletos == 1400.00


def test_viacredi_outro_destinatario_vira_transferencia_familiar():
    """Garante que transferências Viacredi para terceiros viram transferência familiar, e para o titular viram cooperativa."""
    tx_titular = _make_stmttrn("DEBIT", "20261103000000[-3:BRT]", -60.00, "via-01", "Transferência VIACREDI para TITULAR TESTE")
    tx_terceiro = _make_stmttrn("DEBIT", "20261104000000[-3:BRT]", -200.00, "via-02", "Transferência VIACREDI para OUTRA PESSOA")
    content = OFX_TEMPLATE.format(transactions=f"{tx_titular}\n{tx_terceiro}")

    with tempfile.TemporaryDirectory() as tmpdir:
        fpath = os.path.join(tmpdir, "extrato.ofx")
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(content)

        resumos = OFXParserService.processar_diretorio_extratos(
            tmpdir,
            titular_nomes=["TITULAR TESTE"],
        )
        r = resumos["2026-11"]
        assert r.cooperativa_ailos == 60.00
        assert r.moradia_e_familia == 200.00


def test_reversal_nao_polui_agregados():
    """Garante que pares de estorno/reversal são anulados e não poluem agregados."""
    tx_compra = _make_stmttrn("DEBIT", "20261106000000[-3:BRT]", -500.00, "rev-01", "Compra BBAS3")
    tx_estorno = _make_stmttrn("CREDIT", "20261106000000[-3:BRT]", 500.00, "rev-01:reversal", "Compra BBAS3")
    tx_efetiva = _make_stmttrn("DEBIT", "20261107000000[-3:BRT]", -250.00, "rev-02", "Compra BBAS3")
    content = OFX_TEMPLATE.format(transactions=f"{tx_compra}\n{tx_estorno}\n{tx_efetiva}")

    with tempfile.TemporaryDirectory() as tmpdir:
        fpath = os.path.join(tmpdir, "extrato.ofx")
        with open(fpath, "w", encoding="utf-8") as f:
            f.write(content)

        resumos = OFXParserService.processar_diretorio_extratos(tmpdir)
        r = resumos["2026-11"]
        assert r.compras_acoes_barsi == 250.00
        assert r.total_investimentos == 250.00
        assert r.detalhes_ativos.get("BBAS3") == 250.00

