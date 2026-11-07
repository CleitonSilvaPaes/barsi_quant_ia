"""Testes unitários para o parser de extratos bancários OFX."""
from barsi.services.ofx_service import OFXParserService

SAMPLE_OFX = """OFXHEADER:100
<OFX>
<BANKTRANLIST>
<STMTTRN>
<TRNTYPE>CREDIT</TRNTYPE>
<DTPOSTED>20261106000000[-3:BRT]</DTPOSTED>
<TRNAMT>4340.50</TRNAMT>
<FITID>test-01</FITID>
<MEMO>Transferência Recebida - Salário</MEMO>
</STMTTRN>
<STMTTRN>
<TRNTYPE>DEBIT</TRNTYPE>
<DTPOSTED>20261106000000[-3:BRT]</DTPOSTED>
<TRNAMT>-312.40</TRNAMT>
<FITID>test-02</FITID>
<MEMO>Compra de Ações - BBAS3</MEMO>
</STMTTRN>
<STMTTRN>
<TRNTYPE>CREDIT</TRNTYPE>
<DTPOSTED>20261106000000[-3:BRT]</DTPOSTED>
<TRNAMT>50.13</TRNAMT>
<FITID>test-03</FITID>
<MEMO>Crédito em conta - Provento</MEMO>
</STMTTRN>
</BANKTRANLIST>
</OFX>"""

def test_ofx_parser_transactions():
    txs = OFXParserService.parse_ofx_content(SAMPLE_OFX)
    assert len(txs) == 3
    
    # 1. Salário
    assert txs[0].valor == 4340.50
    assert txs[0].categoria == "Salário / Renda Principal"
    
    # 2. Aporte BBAS3
    assert txs[1].valor == -312.40
    assert txs[1].ticker_relacionado == "BBAS3"
    assert txs[1].categoria == "Aporte Ações/FIIs"
    
    # 3. Proventos
    assert txs[2].valor == 50.13
    assert txs[2].categoria == "Proventos B3"

def test_ofx_estorno_e_cancelamento_fii():
    ofx_cancelamento = """OFXHEADER:100
<OFX>
<BANKTRANLIST>
<STMTTRN>
<TRNTYPE>DEBIT</TRNTYPE>
<DTPOSTED>20261106000000[-3:BRT]</DTPOSTED>
<TRNAMT>-622.50</TRNAMT>
<FITID>test-compra-1</FITID>
<MEMO>Compra de FII - MXRF11</MEMO>
</STMTTRN>
<STMTTRN>
<TRNTYPE>CREDIT</TRNTYPE>
<DTPOSTED>20261106000000[-3:BRT]</DTPOSTED>
<TRNAMT>622.50</TRNAMT>
<FITID>test-estorno-1</FITID>
<MEMO>Compra de FII - MXRF11</MEMO>
</STMTTRN>
<STMTTRN>
<TRNTYPE>DEBIT</TRNTYPE>
<DTPOSTED>20261106000000[-3:BRT]</DTPOSTED>
<TRNAMT>-311.95</TRNAMT>
<FITID>test-compra-efetiva</FITID>
<MEMO>Compra de FII - MXRF11</MEMO>
</STMTTRN>
</BANKTRANLIST>
</OFX>"""
    import tempfile, os
    with tempfile.TemporaryDirectory() as tmpdir:
        ofx_file = os.path.join(tmpdir, "extrato.ofx")
        with open(ofx_file, "w", encoding="utf-8") as f:
            f.write(ofx_cancelamento)
        
        resumos = OFXParserService.processar_diretorio_extratos(tmpdir)
        r = resumos["2026-11"]
        # O estorno de 622.50 deve anular o débito de 622.50, restando apenas o aporte efetivo de 311.95
        assert r.detalhes_ativos["MXRF11"] == 311.95
        assert r.compras_fiis == 311.95
        assert r.total_investimentos == 311.95

def test_ofx_idempotencia_duplicacao():
    """Garante que se o mesmo extrato for adicionado duas vezes ou reprocessado, os valores não se multiplicam."""
    import tempfile, os
    with tempfile.TemporaryDirectory() as tmpdir:
        # Cria dois arquivos com o mesmo conteúdo/FITIDs
        for i in [1, 2]:
            fpath = os.path.join(tmpdir, f"extrato_copia_{i}.ofx")
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(SAMPLE_OFX)
        
        resumos = OFXParserService.processar_diretorio_extratos(tmpdir)
        r = resumos["2026-11"]
        # Mesmo com 2 arquivos idênticos, o salário deve ser contado apenas 1 vez (4340.50), e não duplicado
        assert r.salario_entradas == 4340.50
        assert r.compras_acoes_barsi == 312.40

def test_ofx_fatura_cartao_sem_duplicidade():
    """Valida que extrato de cartão não causa contagem duplicada e que fatura isolada é apurada corretamente."""
    ofx_cartao = """OFXHEADER:100
<OFX>
<CREDITCARDMSGSRSV1>
<CCACCTINFO>
<BANKTRANLIST>
<STMTTRN>
<TRNTYPE>DEBIT</TRNTYPE>
<DTPOSTED>20261102000000[-3:BRT]</DTPOSTED>
<TRNAMT>-150.00</TRNAMT>
<FITID>card-01</FITID>
<MEMO>SUPERMERCADO KOCH</MEMO>
</STMTTRN>
<STMTTRN>
<TRNTYPE>DEBIT</TRNTYPE>
<DTPOSTED>20261103000000[-3:BRT]</DTPOSTED>
<TRNAMT>-85.50</TRNAMT>
<FITID>card-02</FITID>
<MEMO>POSTO IPIRANGA</MEMO>
</STMTTRN>
</BANKTRANLIST>
</CCACCTINFO>
</CREDITCARDMSGSRSV1>
</OFX>"""
    import tempfile, os
    with tempfile.TemporaryDirectory() as tmpdir:
        card_file = os.path.join(tmpdir, "fatura_cartao.ofx")
        with open(card_file, "w", encoding="utf-8") as f:
            f.write(ofx_cartao)
        
        # Cenário 1: Apenas fatura de cartão -> apura total_gastos_cartao como fatura
        resumos = OFXParserService.processar_diretorio_extratos(tmpdir)
        r = resumos["2026-11"]
        assert r.faturas_cartao == 235.50
        assert r.total_saidas == 235.50

        # Cenário 2: Adiciona extrato de conta corrente com o pagamento da fatura já consolidado
        conta_file = os.path.join(tmpdir, "extrato_conta.ofx")
        ofx_conta = """OFXHEADER:100
<OFX>
<BANKTRANLIST>
<STMTTRN>
<TRNTYPE>DEBIT</TRNTYPE>
<DTPOSTED>20261108000000[-3:BRT]</DTPOSTED>
<TRNAMT>-235.50</TRNAMT>
<FITID>conta-pagto-fatura</FITID>
<MEMO>Pagamento de fatura Nubank</MEMO>
</STMTTRN>
</BANKTRANLIST>
</OFX>"""
        with open(conta_file, "w", encoding="utf-8") as f:
            f.write(ofx_conta)

        resumos2 = OFXParserService.processar_diretorio_extratos(tmpdir)
        r2 = resumos2["2026-11"]
        # Não deve duplicar (235.50 + 235.50 = 471). Deve permanecer estritamente 235.50!
        assert r2.faturas_cartao == 235.50
        assert r2.total_saidas == 235.50

