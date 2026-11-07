"""Factory Pattern para instanciação desacoplada de Adapters e Ports."""
from barsi.config import Settings
from barsi.ports.market import MarketDataPort
from barsi.ports.news import NewsPort
from barsi.ports.ai import AIPort
from barsi.ports.sheets import SheetsPort
from barsi.ports.history import HistoryPort

from barsi.adapters.market.yfinance_adapter import YFinanceAdapter
from barsi.adapters.market.brapi_adapter import BrapiAdapter
from barsi.adapters.market.cached_adapter import CachedMarketAdapter

from barsi.adapters.news.rss_adapter import GoogleNewsRSSAdapter
from barsi.adapters.news.stub_adapter import StubNewsAdapter

from barsi.adapters.ai.rule_engine_adapter import RuleEngineAdapter
from barsi.adapters.ai.ollama_adapter import OllamaRemoteAdapter

from barsi.adapters.sheets.gspread_adapter import GSpreadAdapter
from barsi.adapters.sheets.xlsx_adapter import XLSXAdapter

from barsi.adapters.history.sqlite_adapter import SQLiteHistoryAdapter
from barsi.adapters.history.r2_mirror_adapter import R2MirrorHistoryAdapter

class AdapterFactory:
    @staticmethod
    def create_market_adapter(cfg: Settings) -> MarketDataPort:
        if cfg.market_provider == "brapi" and cfg.brapi_token:
            raw = BrapiAdapter(token=cfg.brapi_token)
        else:
            raw = YFinanceAdapter()
        return CachedMarketAdapter(raw, cache_dir=cfg.cache_dir)

    @staticmethod
    def create_news_adapter(cfg: Settings) -> NewsPort:
        if cfg.news_provider == "stub":
            return StubNewsAdapter()
        return GoogleNewsRSSAdapter()

    @staticmethod
    def create_ai_adapter(cfg: Settings) -> AIPort:
        if cfg.ai_provider == "ollama_remote":
            return OllamaRemoteAdapter(base_url=cfg.ollama_url, model=cfg.ollama_model)
        return RuleEngineAdapter()

    @staticmethod
    def create_sheets_adapter(cfg: Settings) -> SheetsPort:
        import os
        if cfg.sheets_provider == "gspread" and os.path.exists(cfg.sheets_credentials_json):
            return GSpreadAdapter(
                credentials_path=cfg.sheets_credentials_json,
                spreadsheet_id=cfg.sheets_spreadsheet_id
            )
        return XLSXAdapter(file_path=cfg.local_xlsx_path)

    @staticmethod
    def create_history_adapter(cfg: Settings) -> HistoryPort:
        base = SQLiteHistoryAdapter(db_path=cfg.sqlite_db_path)
        if cfg.r2_enabled and cfg.r2_account_id and cfg.r2_access_key_id:
            return R2MirrorHistoryAdapter(
                inner=base,
                account_id=cfg.r2_account_id,
                access_key_id=cfg.r2_access_key_id,
                secret_access_key=cfg.r2_secret_access_key,
                bucket_name=cfg.r2_bucket_name
            )
        return base

