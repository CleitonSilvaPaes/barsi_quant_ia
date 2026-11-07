"""Adapter de cotação utilizando brapi.dev."""
import httpx
from typing import Optional
from barsi.ports.market import MarketDataPort
from barsi.domain.models import MarketQuote
from barsi.infra.circuit_breaker import CircuitBreaker
from barsi.infra.retry import network_retry
from barsi.infra.logging import logger

class BrapiAdapter(MarketDataPort):
    def __init__(self, token: str = "", breaker: Optional[CircuitBreaker] = None):
        self.token = token
        self.breaker = breaker or CircuitBreaker("brapi_market", failure_threshold=3, recovery_time_sec=60.0)

    async def get_quote(self, ticker: str) -> Optional[MarketQuote]:
        if not self.breaker.can_execute():
            logger.warn("circuit_open_skipping_quote", ticker=ticker, provider="brapi")
            return None

        clean_ticker = ticker.strip().upper()
        url = f"https://brapi.dev/api/quote/{clean_ticker}"
        params = {}
        if self.token:
            params["token"] = self.token

        try:
            @network_retry
            async def _fetch():
                async with httpx.AsyncClient(timeout=6.0) as client:
                    resp = await client.get(url, params=params)
                    resp.raise_for_status()
                    return resp.json()

            data = await _fetch()
            results = data.get("results", [])
            if results and "regularMarketPrice" in results[0]:
                price = float(results[0]["regularMarketPrice"])
                self.breaker.record_success()
                return MarketQuote(ticker=ticker, price=round(price, 2), source="brapi")
        except Exception as exc:
            self.breaker.record_failure()
            logger.error("brapi_quote_failed", ticker=ticker, error=str(exc))
        return None

