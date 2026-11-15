"""Testes para novos comandos do Telegram (/payout, /anomalias) e upload de extratos OFX (Sprint Telegram 1)."""
import os
import pytest
import respx
import httpx
from fastapi.testclient import TestClient

from barsi.api.http import app
from barsi.config import settings
from barsi.adapters.history.budget_repository import BudgetRepository
from barsi.domain.models import Position
from barsi.api.common import invalidate_canonical_positions_cache


@pytest.fixture
def tg_setup(tmp_path, monkeypatch):
    test_db = str(tmp_path / "test_tg.db")
    monkeypatch.setattr(settings, "sqlite_db_path", test_db)
    monkeypatch.setattr(settings, "telegram_chat_id", "987654321")
    monkeypatch.setattr(settings, "telegram_bot_token", "fake_bot_token_123")
    monkeypatch.setattr(settings, "telegram_webhook_secret", "")

    repo = BudgetRepository(test_db)
    repo.persist_positions([
        Position(
            ticker="BBAS3",
            row_index=10,
            empresa="Banco do Brasil",
            setor="Bancos",
            preco_atual=25.0,
            div_proj=2.5,
            preco_teto=41.67,
            margem_seguranca=66.7,
            qtd_antes=100,
        )
    ])
    invalidate_canonical_positions_cache()
    return repo


def test_telegram_webhook_payout_command(tg_setup, monkeypatch):
    """Testa o comando /payout BBAS3 e sua explicabilidade via Telegram."""
    sent_messages = []

    async def _mock_send_telegram(token, chat_id, text):
        sent_messages.append({"token": token, "chat_id": chat_id, "text": text})

    import barsi.api.http as http_mod
    monkeypatch.setattr(http_mod, "send_telegram_message", _mock_send_telegram)

    client = TestClient(app)

    # 1. Sem argumentos
    resp_no_args = client.post("/api/telegram/webhook", json={
        "message": {"chat": {"id": 987654321}, "text": "/payout"}
    })
    assert resp_no_args.status_code == 200
    assert any("Uso correto: `/payout BBAS3`" in m["text"] for m in sent_messages)

    # 2. Com ativo existente
    sent_messages.clear()
    resp_ok = client.post("/api/telegram/webhook", json={
        "message": {"chat": {"id": 987654321}, "text": "/payout BBAS3"}
    })
    assert resp_ok.status_code == 200
    assert len(sent_messages) == 1
    msg = sent_messages[0]["text"]
    assert "Sustentabilidade de Proventos v2 (ML-2)" in msg
    assert "BBAS3" in msg
    assert "Escore:" in msg
    assert "Fatores Principais:" in msg

    # 3. Com ativo inexistente
    sent_messages.clear()
    resp_err = client.post("/api/telegram/webhook", json={
        "message": {"chat": {"id": 987654321}, "text": "/payout MGLU3"}
    })
    assert resp_err.status_code == 200
    assert "não encontrado na carteira" in sent_messages[0]["text"]


def test_telegram_webhook_anomalias_command(tg_setup, monkeypatch):
    """Testa o comando /anomalias listando alertas detectados."""
    sent_messages = []

    async def _mock_send_telegram(token, chat_id, text):
        sent_messages.append({"token": token, "chat_id": chat_id, "text": text})

    import barsi.api.http as http_mod
    monkeypatch.setattr(http_mod, "send_telegram_message", _mock_send_telegram)

    client = TestClient(app)

    # 1. Sem anomalias registradas
    resp_empty = client.post("/api/telegram/webhook", json={
        "message": {"chat": {"id": 987654321}, "text": "/anomalias"}
    })
    assert resp_empty.status_code == 200
    assert any("Nenhuma anomalia financeira" in m["text"] for m in sent_messages)

    # 2. Com anomalia cadastrada
    sent_messages.clear()
    tg_setup.record_thesis_alert(
        ticker="EXTRATO",
        risk_type="ANOMALIA_EXTRATO",
        severity="ALTA",
        headline="Alerta ML-1",
        snippet="Gasto suspeito R$ 45.000,00",
        source="test",
    )

    resp_with_alert = client.post("/api/telegram/webhook", json={
        "message": {"chat": {"id": 987654321}, "text": "/anomalias"}
    })
    assert resp_with_alert.status_code == 200
    assert any("Alerta ML-1" in m["text"] for m in sent_messages)


@respx.mock
def test_telegram_webhook_ofx_document_upload(tg_setup, monkeypatch, tmp_path):
    """Testa o envio de arquivo .ofx como documento no Telegram Bot."""
    sent_messages = []

    async def _mock_send_telegram(token, chat_id, text):
        sent_messages.append({"token": token, "chat_id": chat_id, "text": text})

    import barsi.api.http as http_mod
    monkeypatch.setattr(http_mod, "send_telegram_message", _mock_send_telegram)

    # Mock das chamadas getFile e download do Telegram
    fake_token = "fake_bot_token_123"
    respx.get(f"https://api.telegram.org/bot{fake_token}/getFile?file_id=doc_123").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"file_path": "documents/extrato_teste.ofx"}})
    )

    ofx_content = b"""OFXHEADER:100
DATA:OFXSGML
VERSION:102
SECURITY:NONE
ENCODING:USASCII
CHARSET:1252
COMPRESSION:NONE
OLDFILEKEY:NONE
NEWFILEKEY:NONE
<OFX>
<SIGNONMSGSRSV1>
<SONRS>
<STATUS>
<CODE>0
<SEVERITY>INFO
</STATUS>
<DTSERVER>20260601120000
<LANGUAGE>POR
</SONRS>
</SIGNONMSGSRSV1>
<BANKMSGSRSV1>
<STMTTRNRS>
<TRNUID>1
<STATUS>
<CODE>0
<SEVERITY>INFO
</STATUS>
<STMTRS>
<CURDEF>BRL
<BANKACCTFROM>
<BANKID>001
<ACCTID>12345
<ACCTTYPE>CHECKING
</BANKACCTFROM>
<BANKTRANLIST>
<DTSTART>20260601
<DTEND>20260630
<STMTTRN>
<TRNTYPE>CREDIT
<DTPOSTED>20260605
<TRNAMT>5000.00
<FITID>FIT_TG_001
<MEMO>SALARIO RECEBIDO EMPRESA
</STMTTRN>
<STMTTRN>
<TRNTYPE>DEBIT
<DTPOSTED>20260610
<TRNAMT>-150.00
<FITID>FIT_TG_002
<MEMO>SUPERMERCADO DIA
</STMTTRN>
</BANKTRANLIST>
<LEDGERBAL>
<BALAMT>4850.00
<DTASOF>20260630
</LEDGERBAL>
</STMTRS>
</STMTTRNRS>
</BANKMSGSRSV1>
</OFX>"""

    respx.get(f"https://api.telegram.org/file/bot{fake_token}/documents/extrato_teste.ofx").mock(
        return_value=httpx.Response(200, content=ofx_content)
    )

    client = TestClient(app)

    # 1. Documento não-OFX deve ser rejeitado amigavelmente
    resp_invalid = client.post("/api/telegram/webhook", json={
        "message": {
            "chat": {"id": 987654321},
            "document": {"file_id": "doc_pdf", "file_name": "relatorio.pdf"}
        }
    })
    assert resp_invalid.status_code == 200
    assert any("Envie apenas arquivos de extrato `.ofx`" in m["text"] for m in sent_messages)

    # 2. Documento OFX válido
    sent_messages.clear()
    resp_ofx = client.post("/api/telegram/webhook", json={
        "message": {
            "chat": {"id": 987654321},
            "document": {"file_id": "doc_123", "file_name": "extrato_junho.ofx"}
        }
    })
    assert resp_ofx.status_code == 200
    assert len(sent_messages) == 1
    resp_text = sent_messages[0]["text"]
    assert "Extrato Bancário Processado via Telegram" in resp_text
    assert "extrato_junho.ofx" in resp_text
    assert "Transações Persistidas:" in resp_text


def test_anomalias_command_shows_ticker_specific_alerts(tg_setup, monkeypatch):
    """Anomalia em transação com ticker (BBAS3) deve aparecer em /anomalias."""
    sent_messages = []

    async def _mock_send_telegram(token, chat_id, text):
        sent_messages.append({"token": token, "chat_id": chat_id, "text": text})

    import barsi.api.http as http_mod
    monkeypatch.setattr(http_mod, "send_telegram_message", _mock_send_telegram)

    tg_setup.record_thesis_alert(
        ticker="BBAS3",
        risk_type="ANOMALIA_EXTRATO",
        severity="ALTA",
        headline="Compra suspeita BBAS3",
        snippet="[fit_123] R$ 45.000",
        source="test",
    )

    client = TestClient(app)
    resp = client.post("/api/telegram/webhook", json={
        "message": {"chat": {"id": 987654321}, "text": "/anomalias"}
    })
    assert resp.status_code == 200
    assert any("BBAS3" in m["text"] and "Compra suspeita BBAS3" in m["text"] for m in sent_messages)


@respx.mock
def test_telegram_ofx_upload_with_anomalies(tg_setup, monkeypatch):
    """Upload OFX com anomalias deve invocar EarlyWarningService e reportar no Telegram."""
    sent_messages = []

    async def _mock_send_telegram(token, chat_id, text):
        sent_messages.append({"token": token, "chat_id": chat_id, "text": text})

    import barsi.api.http as http_mod
    monkeypatch.setattr(http_mod, "send_telegram_message", _mock_send_telegram)

    from barsi.services.early_warning_service import EarlyWarningService

    async def _fake_scan(self, transacoes=None, **kwargs):
        return [{
            "descricao": "PIX SUSPEITO",
            "valor": -45000.0,
            "severity": "ALTA",
            "score": 0.92,
            "fitid": "FIT_TG_001"
        }]

    monkeypatch.setattr(EarlyWarningService, "scan_ofx_anomalies", _fake_scan)

    fake_token = "fake_bot_token_123"
    respx.get(f"https://api.telegram.org/bot{fake_token}/getFile?file_id=doc_anom").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"file_path": "documents/extrato_anom.ofx"}})
    )
    respx.get(f"https://api.telegram.org/file/bot{fake_token}/documents/extrato_anom.ofx").mock(
        return_value=httpx.Response(200, content=b"OFXHEADER:100\nDATA:OFXSGML\nVERSION:102\n<OFX>\n<BANKTRANLIST>\n<STMTTRN><TRNAMT>-45000.00</TRNAMT></STMTTRN>\n</BANKTRANLIST>\n</OFX>")
    )

    client = TestClient(app)
    resp = client.post("/api/telegram/webhook", json={
        "message": {
            "chat": {"id": 987654321},
            "document": {"file_id": "doc_anom", "file_name": "extrato_anom.ofx"}
        }
    })
    assert resp.status_code == 200
    assert len(sent_messages) == 1
    text = sent_messages[0]["text"]
    assert "1 anomalia(s) detectada(s)" in text
    assert "PIX SUSPEITO" in text


@respx.mock
def test_telegram_webhook_ofx_download_error_handling(tg_setup, monkeypatch):
    """Falha de download no Telegram (ex: HTTP 404) é tratada com resposta amigável."""
    sent_messages = []

    async def _mock_send_telegram(token, chat_id, text):
        sent_messages.append({"token": token, "chat_id": chat_id, "text": text})

    import barsi.api.http as http_mod
    monkeypatch.setattr(http_mod, "send_telegram_message", _mock_send_telegram)

    fake_token = "fake_bot_token_123"
    respx.get(f"https://api.telegram.org/bot{fake_token}/getFile?file_id=doc_err").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"file_path": "documents/inexistente.ofx"}})
    )
    respx.get(f"https://api.telegram.org/file/bot{fake_token}/documents/inexistente.ofx").mock(
        return_value=httpx.Response(404, content=b"Not Found")
    )

    client = TestClient(app)
    resp = client.post("/api/telegram/webhook", json={
        "message": {
            "chat": {"id": 987654321},
            "document": {"file_id": "doc_err", "file_name": "extrato_err.ofx"}
        }
    })
    assert resp.status_code == 200
    assert any("Falha ao baixar arquivo" in m["text"] for m in sent_messages)


@respx.mock
def test_telegram_webhook_ofx_signature_validation(tg_setup, monkeypatch):
    """Arquivo sem cabeçalho <OFX> ou OFXHEADER deve ser rejeitado por assinatura inválida."""
    sent_messages = []

    async def _mock_send_telegram(token, chat_id, text):
        sent_messages.append({"token": token, "chat_id": chat_id, "text": text})

    import barsi.api.http as http_mod
    monkeypatch.setattr(http_mod, "send_telegram_message", _mock_send_telegram)

    fake_token = "fake_bot_token_123"
    respx.get(f"https://api.telegram.org/bot{fake_token}/getFile?file_id=doc_html").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"file_path": "documents/fake.ofx"}})
    )
    respx.get(f"https://api.telegram.org/file/bot{fake_token}/documents/fake.ofx").mock(
        return_value=httpx.Response(200, content=b"<html><body>Erro no proxy corporativo</body></html>")
    )

    client = TestClient(app)
    resp = client.post("/api/telegram/webhook", json={
        "message": {
            "chat": {"id": 987654321},
            "document": {"file_id": "doc_html", "file_name": "fake.ofx"}
        }
    })
    assert resp.status_code == 200
    assert any("não possui cabeçalho ou assinatura OFX válida" in m["text"] for m in sent_messages)


def test_telegram_webhook_health_command(tg_setup, monkeypatch):
    """Testa comando /health reportando status dos modelos ML e SQLite."""
    sent_messages = []

    async def _mock_send_telegram(token, chat_id, text):
        sent_messages.append({"token": token, "chat_id": chat_id, "text": text})

    import barsi.api.http as http_mod
    monkeypatch.setattr(http_mod, "send_telegram_message", _mock_send_telegram)

    client = TestClient(app)
    resp = client.post("/api/telegram/webhook", json={
        "message": {"chat": {"id": 987654321}, "text": "/health"}
    })
    assert resp.status_code == 200
    assert len(sent_messages) == 1
    text = sent_messages[0]["text"]
    assert "Diagnóstico de Saúde & Status Operacional" in text
    assert "Isolation Forest (ML-1):" in text
    assert "Payout Scorer (ML-2):" in text
    assert "SQLite Canônico:" in text


def test_telegram_webhook_backup_command(tg_setup, monkeypatch):
    """Testa comando /backup enviando documento SQLite diretamente pelo Telegram."""
    sent_messages = []
    sent_docs = []

    async def _mock_send_telegram(token, chat_id, text):
        sent_messages.append({"token": token, "chat_id": chat_id, "text": text})

    import barsi.api.http as http_mod
    monkeypatch.setattr(http_mod, "send_telegram_message", _mock_send_telegram)

    from barsi.adapters.notifications.telegram_adapter import TelegramNotifier

    async def _mock_send_doc(self, filename, content, caption=None, content_type=None):
        sent_docs.append({
            "filename": filename,
            "content": content,
            "caption": caption,
            "content_type": content_type
        })
        return True

    monkeypatch.setattr(TelegramNotifier, "send_document", _mock_send_doc)

    client = TestClient(app)
    resp = client.post("/api/telegram/webhook", json={
        "message": {"chat": {"id": 987654321}, "text": "/backup"}
    })
    assert resp.status_code == 200
    assert len(sent_docs) == 1
    doc = sent_docs[0]
    assert doc["filename"].startswith("barsi_memory_backup_")
    assert doc["filename"].endswith(".db")
    assert len(doc["content"]) > 0
    assert "Backup Canônico SQLite" in doc["caption"]


def test_telegram_webhook_fechamento_command(tg_setup, monkeypatch):
    """Testa comando /fechamento gerando relatório executivo e enviando via Telegram."""
    sent_messages = []
    closing_called = []

    async def _mock_send_telegram(token, chat_id, text):
        sent_messages.append({"token": token, "chat_id": chat_id, "text": text})

    import barsi.api.http as http_mod
    monkeypatch.setattr(http_mod, "send_telegram_message", _mock_send_telegram)

    from barsi.services.monthly_closing_service import MonthlyClosingService

    async def _mock_closing(self, notifier, mes_ano=None):
        closing_called.append(mes_ano)
        return True

    monkeypatch.setattr(MonthlyClosingService, "send_closing_via_telegram", _mock_closing)

    client = TestClient(app)
    resp = client.post("/api/telegram/webhook", json={
        "message": {"chat": {"id": 987654321}, "text": "/fechamento 2026-06"}
    })
    assert resp.status_code == 200
    assert len(closing_called) == 1
    assert closing_called[0] == "2026-06"
    assert any("Gerando relatório executivo" in m["text"] for m in sent_messages)


