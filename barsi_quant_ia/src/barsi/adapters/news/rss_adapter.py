"""Adapter de notícias utilizando Google News RSS (B3 / Proventos)."""
import re
import httpx
import urllib.parse
import xml.etree.ElementTree as ET
from barsi.ports.news import NewsPort
from barsi.domain.models import NewsSummary
from barsi.infra.logging import logger
from barsi.infra.retry import network_retry

class GoogleNewsRSSAdapter(NewsPort):
    def __init__(self, timeout_sec: float = 6.0):
        self.timeout_sec = timeout_sec

    async def get_latest_news(self, ticker: str) -> NewsSummary:
        termo = f"{ticker} dividendos proventos B3"
        url = f"https://news.google.com/rss/search?q={urllib.parse.quote(termo)}&hl=pt-BR&gl=BR&ceid=BR:pt-419"
        
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"
        }

        try:
            @network_retry
            async def _fetch():
                async with httpx.AsyncClient(timeout=self.timeout_sec, headers=headers) as client:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    return resp.text

            content = await _fetch()
            root = ET.fromstring(content)
            items = root.findall("./channel/item")
            snippets = []
            for item in items[:2]:
                title = item.find("title")
                desc = item.find("description")
                parts = []
                if title is not None and title.text:
                    parts.append(title.text.strip())
                if desc is not None and desc.text:
                    clean_desc = re.sub(r'<[^>]+>', ' ', desc.text).strip()
                    clean_desc = " ".join(clean_desc.split())
                    if clean_desc and clean_desc not in (title.text if title is not None and title.text else ""):
                        parts.append(clean_desc[:180])
                if parts:
                    snippets.append(" — ".join(parts))

            if snippets:
                return NewsSummary(ticker=ticker, headlines=" | ".join(snippets), source="google_rss")
        except Exception as exc:
            logger.warn("news_rss_fetch_failed", ticker=ticker, error=str(exc))

        return NewsSummary(
            ticker=ticker,
            headlines="Operações e proventos regulares sem fatos anômalos.",
            source="fallback_default"
        )

