"""Porta formal para Provedores de Dados de Mercado."""
from typing import Protocol, runtime_checkable, Optional
from barsi.domain.models import MarketQuote

@runtime_checkable
class MarketDataPort(Protocol):
    async def get_quote(self, ticker: str) -> Optional[MarketQuote]:
        """Recupera cotação atualizada para um ticker na B3."""
        ...

