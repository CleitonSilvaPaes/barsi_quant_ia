"""Porta formal para Provedores de Dados de Mercado."""
from dataclasses import dataclass
from typing import Protocol, runtime_checkable, Optional, List
from barsi.domain.models import MarketQuote


@dataclass
class HistoryBar:
    """Representa uma barra histórica de preço e volume."""
    close: float
    volume: float = 0.0
    date: str = ""


@runtime_checkable
class MarketDataPort(Protocol):
    async def get_quote(self, ticker: str) -> Optional[MarketQuote]:
        """Recupera cotação atualizada para um ticker na B3."""
        ...

    async def get_history(self, ticker: str, period: str = "1mo") -> List[HistoryBar]:
        """Recupera histórico de barras diárias para um ticker na B3."""
        ...
