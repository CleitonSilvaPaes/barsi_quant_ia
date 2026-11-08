"""Porta formal para Provedores de Notificações / Alertas."""
from typing import Protocol, runtime_checkable

@runtime_checkable
class NotificationsPort(Protocol):
    async def notify(self, title: str, body: str, level: str = "info") -> bool:
        """Envia uma notificação assíncrona. Retorna True em caso de sucesso."""
        ...
