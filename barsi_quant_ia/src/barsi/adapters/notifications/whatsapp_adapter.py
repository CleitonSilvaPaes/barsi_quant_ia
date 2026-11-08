"""Adaptadores de Notificação via WhatsApp e Notificador Múltiplo."""
import urllib.parse
import httpx
import structlog
from typing import List, Optional
from barsi.ports.notifications import NotificationsPort

logger = structlog.get_logger(__name__)

ICON_MAP = {
    "alert": "🚨",
    "alerta": "🚨",
    "warn": "⚠️",
    "warning": "⚠️",
    "success": "✅",
    "info": "ℹ️",
}


class WhatsAppCallMeBotNotifier(NotificationsPort):
    """Notificador pessoal gratuito via gateway CallMeBot (sem necessidade de servidor próprio).

    Para ativar:
    1. Adicione o contato +34 644 59 71 67 ou consulte https://www.callmebot.com/blog/free-api-whatsapp-messages/
    2. Envie 'I allow callmebot to send me messages'
    3. Receba sua apikey pessoal e configure no .env
    """

    def __init__(self, phone: str, api_key: str, timeout_sec: float = 10.0):
        self.phone = phone.replace("+", "").replace("-", "").replace(" ", "").strip()
        self.api_key = api_key.strip()
        self.timeout_sec = timeout_sec
        self.base_url = "https://api.callmebot.com/whatsapp.php"

    async def notify(self, title: str, body: str, level: str = "info") -> bool:
        if not self.phone or not self.api_key:
            logger.warning("callmebot_credentials_missing", phone=bool(self.phone))
            return False

        icon = ICON_MAP.get(level.lower(), "ℹ️")
        full_text = f"{icon} *{title}*\n\n{body}"

        params = {
            "phone": self.phone,
            "text": full_text,
            "apikey": self.api_key,
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout_sec) as client:
                resp = await client.get(self.base_url, params=params)
                if resp.status_code == 200:
                    logger.info("whatsapp_callmebot_sent", title=title, level=level)
                    return True
                else:
                    logger.warning(
                        "whatsapp_callmebot_error_status",
                        status_code=resp.status_code,
                        response=resp.text,
                    )
                    return False
        except Exception as exc:
            logger.error("whatsapp_callmebot_exception", title=title, error=str(exc))
            return False


class WhatsAppEvolutionNotifier(NotificationsPort):
    """Notificador via Evolution API (Baileys / WhatsApp Web Self-Hosted em Docker).

    Ideal para quem roda um microservice local na TV Box ou PC via Docker.
    """

    def __init__(
        self,
        base_url: str,
        instance: str,
        api_key: str,
        recipient: str,
        timeout_sec: float = 10.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.instance = instance.strip()
        self.api_key = api_key.strip()
        self.recipient = recipient.replace("+", "").replace("-", "").replace(" ", "").strip()
        self.timeout_sec = timeout_sec

    async def notify(self, title: str, body: str, level: str = "info") -> bool:
        if not self.base_url or not self.instance or not self.api_key or not self.recipient:
            logger.warning("evolution_api_credentials_missing")
            return False

        icon = ICON_MAP.get(level.lower(), "ℹ️")
        full_text = f"{icon} *{title}*\n\n{body}"

        url = f"{self.base_url}/message/sendText/{self.instance}"
        headers = {
            "apikey": self.api_key,
            "Content-Type": "application/json",
        }
        payload = {
            "number": self.recipient,
            "text": full_text,
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout_sec) as client:
                resp = await client.post(url, json=payload, headers=headers)
                if resp.status_code in (200, 201):
                    logger.info("whatsapp_evolution_sent", title=title, level=level)
                    return True
                else:
                    logger.warning(
                        "whatsapp_evolution_error_status",
                        status_code=resp.status_code,
                        response=resp.text,
                    )
                    return False
        except Exception as exc:
            logger.error("whatsapp_evolution_exception", title=title, error=str(exc))
            return False


class MultiNotifier(NotificationsPort):
    """Encaminha alertas simultaneamente para múltiplos canais (Telegram, WhatsApp, etc)."""

    def __init__(self, notifiers: List[NotificationsPort]):
        self.notifiers = notifiers

    async def notify(self, title: str, body: str, level: str = "info") -> bool:
        import asyncio
        if not self.notifiers:
            return True

        tasks = [n.notify(title, body, level=level) for n in self.notifiers]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Sucesso se ao menos um dos canais ativos confirmou envio
        sucessos = sum(1 for r in results if r is True)
        return sucessos > 0
