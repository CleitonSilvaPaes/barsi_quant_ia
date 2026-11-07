"""Stub de notícias para testes e ambientes offline."""
from barsi.ports.news import NewsPort
from barsi.domain.models import NewsSummary

class StubNewsAdapter(NewsPort):
    def __init__(self, fixed_headlines: str = "Operações regulares."):
        self.fixed_headlines = fixed_headlines

    async def get_latest_news(self, ticker: str) -> NewsSummary:
        return NewsSummary(ticker=ticker, headlines=self.fixed_headlines, source="stub")

