"""Adapter de cotação utilizando brapi.dev."""
import httpx
from datetime import datetime as dt_cls
from typing import Optional, List
from barsi.ports.market import MarketDataPort, HistoryBar
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

    async def get_history(self, ticker: str, period: str = "1mo") -> List[HistoryBar]:
        if not self.breaker.can_execute():
            logger.warn("circuit_open_skipping_history", ticker=ticker, provider="brapi")
            return []

        clean_ticker = ticker.strip().upper()
        range_param = period if period in ("1d", "5d", "1mo", "3mo", "6mo", "1y", "2y", "5y") else "1mo"
        url = f"https://brapi.dev/api/quote/{clean_ticker}"
        params = {"range": range_param, "interval": "1d"}
        if self.token:
            params["token"] = self.token

        try:
            @network_retry
            async def _fetch():
                async with httpx.AsyncClient(timeout=8.0) as client:
                    resp = await client.get(url, params=params)
                    resp.raise_for_status()
                    return resp.json()

            data = await _fetch()
            results = data.get("results", [])
            if results:
                history_data = results[0].get("historicalDataPrice", [])
                bars = []
                for item in history_data:
                    close = item.get("close")
                    if close is not None:
                        date_str = ""
                        date_val = item.get("date")
                        if date_val:
                            try:
                                date_str = dt_cls.fromtimestamp(date_val).strftime("%Y-%m-%d")
                            except Exception:
                                date_str = str(date_val)
                        bars.append(
                            HistoryBar(
                                close=float(close),
                                volume=float(item.get("volume", 0.0) or 0.0),
                                date=date_str
                            )
                        )
                if bars:
                    self.breaker.record_success()
                    return bars
        except Exception as exc:
            self.breaker.record_failure()
            logger.error("brapi_history_failed", ticker=ticker, error=str(exc))
        return []
