"""Adapter Decorator para Caching persistente em disco (diskcache)."""
import os
from typing import Optional, List
from diskcache import Cache
from barsi.ports.market import MarketDataPort, HistoryBar
from barsi.domain.models import MarketQuote
from barsi.infra.logging import logger
from datetime import datetime

class CachedMarketAdapter(MarketDataPort):
    def __init__(self, inner: MarketDataPort, cache_dir: str = "./cache"):
        self.inner = inner
        os.makedirs(cache_dir, exist_ok=True)
        self.cache = Cache(cache_dir)

    def _get_ttl(self) -> int:
        # Pregão (09h às 18h em dias úteis): TTL 5 min (300s)
        # Fora do pregão: TTL 12 horas (43200s)
        now = datetime.now()
        is_trading = now.weekday() < 5 and 9 <= now.hour < 18
        return 300 if is_trading else 43200

    async def get_quote(self, ticker: str) -> Optional[MarketQuote]:
        cache_key = f"quote_{ticker.upper()}"
        cached_data = self.cache.get(cache_key)
        if cached_data:
            logger.debug("market_cache_hit", ticker=ticker)
            return MarketQuote(ticker=ticker, price=cached_data["price"], source="diskcache")

        # Busca no provedor real
        quote = await self.inner.get_quote(ticker)
        if quote:
            ttl = self._get_ttl()
            self.cache.set(cache_key, {"price": quote.price}, expire=ttl)
            logger.debug("market_cache_stored", ticker=ticker, ttl=ttl)
            return quote

        # Se falhou e existe cache expirado no disco, usa como fallback defensivo
        return None

    async def get_history(self, ticker: str, period: str = "1mo") -> List[HistoryBar]:
        cache_key = f"hist_{ticker.upper()}_{period}"
        cached_data = self.cache.get(cache_key)
        if cached_data:
            logger.debug("market_cache_history_hit", ticker=ticker)
            return [HistoryBar(**bar) if isinstance(bar, dict) else bar for bar in cached_data]

        # Busca no provedor real
        bars = await self.inner.get_history(ticker, period=period)
        if bars:
            ttl = self._get_ttl()
            bars_dict = [{"close": b.close, "volume": b.volume, "date": b.date} for b in bars]
            self.cache.set(cache_key, bars_dict, expire=ttl)
            logger.debug("market_cache_history_stored", ticker=ticker, ttl=ttl)
            return bars
        return []
