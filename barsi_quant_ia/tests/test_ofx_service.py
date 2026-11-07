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
