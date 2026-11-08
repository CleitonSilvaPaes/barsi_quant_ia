"""Testes de Contrato e Integração para Adapters e Pipeline Barsi."""
import pytest
from barsi.ports.market import MarketDataPort
from barsi.ports.news import NewsPort
from barsi.ports.ai import AIPort
from barsi.ports.sheets import SheetsPort
from barsi.ports.history import HistoryPort

from barsi.domain.models import Position, MarketQuote
from barsi.adapters.ai.rule_engine_adapter import RuleEngineAdapter
from barsi.adapters.news.stub_adapter import StubNewsAdapter
from barsi.adapters.history.sqlite_adapter import SQLiteHistoryAdapter
from barsi.services.sync_service import SyncService

class MockMarketAdapter(MarketDataPort):
    async def get_quote(self, ticker: str):
        return MarketQuote(ticker=ticker, price=40.0, source="mock")

class MockSheetsAdapter(SheetsPort):
    def __init__(self):
        self.positions = [
            Position(ticker="BBAS3", row_index=7, preco_atual=25.0, div_proj=3.0),
            Position(ticker="TAEE11", row_index=8, preco_atual=35.0, div_proj=2.5)
        ]
        self.updated = False

    async def load_positions(self):
        return self.positions

    async def update_positions(self, positions):
        self.positions = positions
        self.updated = True

def test_protocol_conformance(tmp_path):
    # Verifica runtime_checkable dos Protocols
    assert isinstance(RuleEngineAdapter(), AIPort)
    assert isinstance(StubNewsAdapter(), NewsPort)
    assert isinstance(MockMarketAdapter(), MarketDataPort)
    assert isinstance(MockSheetsAdapter(), SheetsPort)
    
    db_file = tmp_path / "test_memory.db"
    assert isinstance(SQLiteHistoryAdapter(str(db_file)), HistoryPort)

def test_sync_service_pipeline_end_to_end(tmp_path):
    import asyncio
    async def _test():
        db_file = tmp_path / "test_sync.db"
        history = SQLiteHistoryAdapter(str(db_file))
        market = MockMarketAdapter()
        news = StubNewsAdapter()
        ai = RuleEngineAdapter()
        sheets = MockSheetsAdapter()

        service = SyncService(
            market=market,
            news=news,
            ai=ai,
            sheets=sheets,
            history=history,
            max_concurrency=2
        )

        summary = await service.execute_run(dry_run=False)
        assert summary.tickers_analyzed == 2
        assert summary.positions_updated == 2
        assert len(summary.errors) == 0
        assert sheets.updated is True

        # Verifica se os preços foram atualizados e teto calculado
        assert sheets.positions[0].preco_atual == 40.0
        assert sheets.positions[0].preco_teto == 50.0  # 3.0 / 0.06
        assert "ACUMULAR" in sheets.positions[0].insight_atual

    asyncio.run(_test())

def test_brapi_adapter_with_respx():
    import respx
    import httpx
    import asyncio
    from barsi.adapters.market.brapi_adapter import BrapiAdapter

    async def _test():
        adapter = BrapiAdapter(token="test_token")
        with respx.mock(base_url="https://brapi.dev") as respx_mock:
            respx_mock.get("/api/quote/BBAS3").mock(
                return_value=httpx.Response(200, json={
                    "results": [{"symbol": "BBAS3", "regularMarketPrice": 28.50}]
                })
            )
            quote = await adapter.get_quote("BBAS3")
            assert quote is not None
            assert quote.ticker == "BBAS3"
            assert quote.price == 28.50
            assert quote.source == "brapi"

    asyncio.run(_test())

def test_rss_adapter_with_respx():
    import respx
    import httpx
    import asyncio
    from barsi.adapters.news.rss_adapter import GoogleNewsRSSAdapter

    async def _test():
        adapter = GoogleNewsRSSAdapter()
        fake_xml = """<?xml version="1.0" encoding="UTF-8"?>
        <rss version="2.0">
          <channel>
            <item><title>BBAS3 anuncia R$ 2 bi em proventos</title></item>
            <item><title>Banco do Brasil divulga balanço trimestral</title></item>
          </channel>
        </rss>"""
        with respx.mock(base_url="https://news.google.com") as respx_mock:
            respx_mock.get(respx.patterns.M("/rss/search")).mock(
                return_value=httpx.Response(200, text=fake_xml)
            )
            news = await adapter.get_latest_news("BBAS3")
            assert "BBAS3 anuncia R$ 2 bi" in news.headlines
            assert news.source == "google_rss"

    asyncio.run(_test())


def test_save_carteira_edits_allows_editing_qtd_atual():
    """Valida que o usuário pode editar Qtd Atual (qtd_antes) e Aporte (qtd_aporte), recalculando total e valores."""
    from barsi.api.http import EditItem, SaveEditsRequest
    from barsi.domain.rules import atualizar_posicao_quant

    p = Position(
        ticker="BBAS3",
        row_index=10,
        qtd_antes=55,
        qtd_aporte=0,
        qtd_total=55,
        preco_atual=17.13,
        div_proj=2.30,
    )
    positions = [p]
    pos_map = {p.ticker: p for p in positions}

    # Usuário ajusta BBAS3 de 55 para 37 em carteira atual, com aporte de 18
    item = EditItem(
        ticker="BBAS3",
        row_index=10,
        div_proj=2.30,
        qtd_antes=37,
        qtd_aporte=18,
        feedback="Priorizar compras com margem > 15%",
    )

    p_target = pos_map[item.ticker]
    p_target.div_proj = item.div_proj
    if item.qtd_antes is not None:
        p_target.qtd_antes = max(0, item.qtd_antes)
    if item.qtd_aporte is not None:
        p_target.qtd_aporte = max(0, item.qtd_aporte)
    p_target.qtd_total = p_target.qtd_antes + p_target.qtd_aporte
    p_target.valor_total = round(p_target.qtd_total * p_target.preco_atual, 2)
    p_target.total_aporte = round(p_target.qtd_aporte * p_target.preco_atual, 2)
    p_target.feedback_usuario = item.feedback
    atualizar_posicao_quant(p_target, p_target.preco_atual, p_target.div_proj)

    assert p_target.qtd_antes == 37
    assert p_target.qtd_aporte == 18
    assert p_target.qtd_total == 55
    assert p_target.valor_total == 942.15
    assert p_target.total_aporte == 308.34
    assert round(p_target.qtd_antes * p_target.preco_atual, 2) == 633.81

