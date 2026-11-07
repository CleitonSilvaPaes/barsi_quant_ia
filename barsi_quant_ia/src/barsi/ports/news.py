"""Porta formal para Provedores de Notícias e Fatos Relevantes."""
from typing import Protocol, runtime_checkable
from barsi.domain.models import NewsSummary

@runtime_checkable
class NewsPort(Protocol):
    async def get_latest_news(self, ticker: str) -> NewsSummary:
        """Busca notícias e proventos recentes para o ativo."""
        ...

