"""Adapter de cotação utilizando Yahoo Finance (yfinance)."""
import asyncio
from typing import Optional
from barsi.ports.market import MarketDataPort
from barsi.domain.models import MarketQuote
from barsi.infra.circuit_breaker import CircuitBreaker
from barsi.infra.logging import logger
import yfinance as yf

class YFinanceAdapter(MarketDataPort):
    def __init__(self, breaker: Optional[CircuitBreaker] = None):
        self.breaker = breaker or CircuitBreaker("yfinance_market", failure_threshold=3, recovery_time_sec=60.0)

    async def get_quote(self, ticker: str) -> Optional[MarketQuote]:
        if not self.breaker.can_execute():
            logger.warn("circuit_open_skipping_quote", ticker=ticker, provider="yfinance")
            return None

        simbolo = f"{ticker.strip().upper()}.SA"
        try:
            # yfinance é síncrono internamente, executamos em thread pool para não bloquear o event loop
            def _fetch():
                t = yf.Ticker(simbolo)
                return t.fast_info.get("lastPrice")

            price = await asyncio.to_thread(_fetch)
            if price and price > 0:
                self.breaker.record_success()
                return MarketQuote(ticker=ticker, price=round(float(price), 2), source="yfinance")
        except Exception as exc:
            self.breaker.record_failure()
            logger.error("yfinance_quote_failed", ticker=ticker, error=str(exc))
        return None

