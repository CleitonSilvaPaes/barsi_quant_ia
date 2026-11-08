"""Adapters de Notificação: Telegram e NoOp (fallback seguro)."""
import httpx
import structlog
from typing import Optional
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


class TelegramNotifier(NotificationsPort):
    """Adaptador de notificações proativas via Bot do Telegram."""

    def __init__(self, bot_token: str, chat_id: str, timeout_sec: float = 10.0):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.timeout_sec = timeout_sec
        self.url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"

    async def notify(self, title: str, body: str, level: str = "info") -> bool:
        if not self.bot_token or not self.chat_id:
            logger.warning("telegram_credentials_missing", chat_id=bool(self.chat_id))
            return False

        icon = ICON_MAP.get(level.lower(), "ℹ️")
        text = f"{icon} *{title}*\n\n{body}"

        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "Markdown",
            "disable_web_page_preview": True,
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout_sec) as client:
                resp = await client.post(self.url, json=payload)
                if resp.status_code == 200:
                    logger.info("telegram_notification_sent", title=title, level=level)
                    return True
                else:
                    logger.warning(
                        "telegram_notification_error_status",
                        status_code=resp.status_code,
                        response=resp.text,
                    )
                    return False
        except Exception as exc:
            logger.error("telegram_notification_exception", title=title, error=str(exc))
            return False


class NoOpNotifier(NotificationsPort):
    """Adaptador No-Operation para quando alertas estão desativados ou sem credenciais."""

    async def notify(self, title: str, body: str, level: str = "info") -> bool:
        logger.debug("noop_notification_skipped", title=title, level=level)
        return True
