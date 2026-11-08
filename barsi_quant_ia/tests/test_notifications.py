"""Testes unitários para o sistema de notificações assíncronas e alertas Telegram."""
import pytest
import respx
import httpx
import json
from barsi.ports.notifications import NotificationsPort
from barsi.adapters.notifications import (
    TelegramNotifier,
    NoOpNotifier,
    WhatsAppCallMeBotNotifier,
    WhatsAppEvolutionNotifier,
    MultiNotifier,
)
from barsi.adapters.factory import AdapterFactory
from barsi.config import Settings
from barsi.domain.models import Position, NewsSummary, InsightResult
from barsi.services.sync_service import SyncService


@pytest.mark.asyncio
async def test_noop_notifier_returns_true():
    notifier = NoOpNotifier()
    assert isinstance(notifier, NotificationsPort)
    ok = await notifier.notify("Título de Teste", "Corpo da mensagem", level="info")
    assert ok is True


@pytest.mark.asyncio
async def test_telegram_notifier_missing_credentials():
    notifier = TelegramNotifier(bot_token="", chat_id="")
    ok = await notifier.notify("Teste", "Corpo")
    assert ok is False


@pytest.mark.asyncio
@respx.mock
async def test_telegram_notifier_success():
    bot_token = "123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11"
    chat_id = "987654321"
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    route = respx.post(url).respond(status_code=200, json={"ok": True, "result": {"message_id": 101}})
    notifier = TelegramNotifier(bot_token=bot_token, chat_id=chat_id)

    ok = await notifier.notify(
        title="BBAS3 em zona de aporte",
        body="Margem 25.0% | Teto R$ 35.00 | Atual R$ 26.00",
        level="alert",
    )
    assert ok is True
    assert route.called
    req = route.calls.last.request
    data = json.loads(req.content)
    assert data["chat_id"] == chat_id
    assert "🚨 *BBAS3 em zona de aporte*" in data["text"]
    assert data["parse_mode"] == "Markdown"


@pytest.mark.asyncio
@respx.mock
async def test_telegram_notifier_http_error():
    bot_token = "123456:fake-token"
    chat_id = "987654321"
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    respx.post(url).respond(status_code=400, json={"ok": False, "description": "Bad Request: chat not found"})
    notifier = TelegramNotifier(bot_token=bot_token, chat_id=chat_id)

    ok = await notifier.notify("Teste", "Corpo", level="info")
    assert ok is False


@pytest.mark.asyncio
@respx.mock
async def test_telegram_notifier_network_failure():
    bot_token = "123456:fake-token"
    chat_id = "987654321"
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    respx.post(url).mock(side_effect=httpx.ConnectError("Failed to connect"))
    notifier = TelegramNotifier(bot_token=bot_token, chat_id=chat_id)

    # Não deve lançar exceção, apenas retornar False e logar
    ok = await notifier.notify("Teste", "Corpo", level="info")
    assert ok is False


@pytest.mark.asyncio
async def test_whatsapp_callmebot_missing_credentials():
    notifier = WhatsAppCallMeBotNotifier(phone="", api_key="")
    ok = await notifier.notify("Teste", "Corpo")
    assert ok is False


@pytest.mark.asyncio
@respx.mock
async def test_whatsapp_callmebot_success():
    phone = "5511999999999"
    api_key = "123456"
    route = respx.get("https://api.callmebot.com/whatsapp.php").respond(status_code=200, text="Message Queued")
    notifier = WhatsAppCallMeBotNotifier(phone=phone, api_key=api_key)

    ok = await notifier.notify("Alerta Barsi", "BBAS3 em zona de aporte", level="alert")
    assert ok is True
    assert route.called
    req = route.calls.last.request
    assert "phone=5511999999999" in str(req.url)
    assert "apikey=123456" in str(req.url)


@pytest.mark.asyncio
@respx.mock
async def test_whatsapp_evolution_success():
    base_url = "http://localhost:8080"
    instance = "barsi"
    url = f"{base_url}/message/sendText/{instance}"
    route = respx.post(url).respond(status_code=201, json={"status": "SUCCESS"})
    notifier = WhatsAppEvolutionNotifier(
        base_url=base_url,
        instance=instance,
        api_key="secret_evo_key",
        recipient="5511988888888",
    )

    ok = await notifier.notify("Alerta Barsi", "ITUB4 em zona de aporte", level="alert")
    assert ok is True
    assert route.called
    req = route.calls.last.request
    assert req.headers["apikey"] == "secret_evo_key"


@pytest.mark.asyncio
async def test_multi_notifier_dispatches_to_all():
    mock1 = MockNotifier()
    mock2 = MockNotifier()
    multi = MultiNotifier([mock1, mock2])

    ok = await multi.notify("Título Multi", "Corpo Multi", level="info")
    assert ok is True
    assert len(mock1.notifications) == 1
    assert len(mock2.notifications) == 1


def test_adapter_factory_notification_fallback():
    # Desativado por default -> NoOp
    cfg = Settings(telegram_alerts_enabled=False)
    adapter = AdapterFactory.create_notification_adapter(cfg)
    assert isinstance(adapter, NoOpNotifier)

    # Ativado mas sem credenciais -> NoOp
    cfg_empty = Settings(telegram_alerts_enabled=True, telegram_bot_token="", telegram_chat_id="")
    adapter_empty = AdapterFactory.create_notification_adapter(cfg_empty)
    assert isinstance(adapter_empty, NoOpNotifier)

    # Ativado com credenciais -> TelegramNotifier
    cfg_active = Settings(
        telegram_alerts_enabled=True,
        telegram_bot_token="tok_test",
        telegram_chat_id="chat_test",
        whatsapp_alerts_enabled=False,
    )
    adapter_active = AdapterFactory.create_notification_adapter(cfg_active)
    assert isinstance(adapter_active, TelegramNotifier)

    # WhatsApp CallMeBot
    cfg_wa = Settings(
        telegram_alerts_enabled=False,
        whatsapp_alerts_enabled=True,
        whatsapp_provider="callmebot",
        whatsapp_phone="5511999999999",
        whatsapp_apikey="123456",
    )
    adapter_wa = AdapterFactory.create_notification_adapter(cfg_wa)
    assert isinstance(adapter_wa, WhatsAppCallMeBotNotifier)

    # Ambos ativados -> MultiNotifier
    cfg_multi = Settings(
        telegram_alerts_enabled=True,
        telegram_bot_token="tok_test",
        telegram_chat_id="chat_test",
        whatsapp_alerts_enabled=True,
        whatsapp_provider="callmebot",
        whatsapp_phone="5511999999999",
        whatsapp_apikey="123456",
    )
    adapter_multi = AdapterFactory.create_notification_adapter(cfg_multi)
    assert isinstance(adapter_multi, MultiNotifier)


class DummyMarket:
    async def get_quote(self, ticker):
        return None


class DummyNews:
    async def get_latest_news(self, ticker):
        return NewsSummary(ticker=ticker, headlines="Resultados trimestrais sólidos.")


class DummyAI:
    async def generate_insight(self, posicao, noticias, memoria_passada, feedback_usuario):
        return InsightResult(
            ticker=posicao.ticker,
            insight="Ativo consolidado no método Barsi.",
            acao_sugerida="ACUMULAR",
            provider="rule_engine",
        )


class DummySheets:
    def __init__(self, positions):
        self.positions = positions

    async def load_positions(self):
        return self.positions

    async def update_positions(self, positions):
        pass


class DummyHistory:
    async def get_last_analysis(self, ticker: str):
        return None

    async def save_analysis(self, **kwargs):
        pass

    async def record_run(self, summary):
        pass


class MockNotifier:
    def __init__(self):
        self.notifications = []

    async def notify(self, title, body, level="info"):
        self.notifications.append((title, body, level))
        return True


@pytest.mark.asyncio
async def test_sync_service_fires_notification_on_acumular_high_margin():
    notifier = MockNotifier()
    pos = Position(
        ticker="BBAS3",
        row_index=2,
        empresa="Banco do Brasil",
        setor="Bancos",
        preco_atual=25.00,
        preco_teto=35.00,
        div_proj=2.10,
        status_barsi="ACUMULAR",
        margem_seguranca=28.57,
    )
    sheets = DummySheets([pos])
    service = SyncService(
        market=DummyMarket(),
        news=DummyNews(),
        ai=DummyAI(),
        sheets=sheets,
        history=DummyHistory(),
        notifier=notifier,
    )

    await service.execute_run(dry_run=False)
    assert len(notifier.notifications) == 1
    title, body, level = notifier.notifications[0]
    assert "BBAS3" in title
    assert level == "alert"


@pytest.mark.asyncio
async def test_sync_service_does_not_fire_notification_in_dry_run():
    notifier = MockNotifier()
    pos = Position(
        ticker="BBAS3",
        row_index=2,
        empresa="Banco do Brasil",
        setor="Bancos",
        preco_atual=25.00,
        preco_teto=35.00,
        div_proj=2.10,
        status_barsi="ACUMULAR",
        margem_seguranca=28.57,
    )
    sheets = DummySheets([pos])
    service = SyncService(
        market=DummyMarket(),
        news=DummyNews(),
        ai=DummyAI(),
        sheets=sheets,
        history=DummyHistory(),
        notifier=notifier,
    )

    await service.execute_run(dry_run=True)
    assert len(notifier.notifications) == 0


@pytest.mark.asyncio
async def test_sync_service_does_not_fire_when_margin_is_low():
    notifier = MockNotifier()
    pos = Position(
        ticker="BBAS3",
        row_index=2,
        empresa="Banco do Brasil",
        setor="Bancos",
        preco_atual=32.00,
        preco_teto=35.00,
        div_proj=2.10,
        status_barsi="AGUARDAR",
        margem_seguranca=8.57,  # abaixo de 20%
    )
    sheets = DummySheets([pos])
    service = SyncService(
        market=DummyMarket(),
        news=DummyNews(),
        ai=DummyAI(),
        sheets=sheets,
        history=DummyHistory(),
        notifier=notifier,
    )

    await service.execute_run(dry_run=False)
    assert len(notifier.notifications) == 0
