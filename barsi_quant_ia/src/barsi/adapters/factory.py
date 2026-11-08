"""Factory Pattern para instanciação desacoplada de Adapters e Ports."""
from barsi.config import Settings
from barsi.ports.market import MarketDataPort
from barsi.ports.news import NewsPort
from barsi.ports.ai import AIPort
from barsi.ports.sheets import SheetsPort
from barsi.ports.history import HistoryPort
from barsi.ports.notifications import NotificationsPort

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
        cred_path = cfg.sheets_credentials_json
        if not os.path.exists(cred_path):
            candidates = [
                "credentials.json",
                "barsi_quant_ia/credentials.json",
                os.path.join(os.path.dirname(__file__), "../../../credentials.json"),
                os.path.join(os.path.dirname(__file__), "../../../barsi_quant_ia/credentials.json"),
                os.path.join(os.getcwd(), "credentials.json"),
                os.path.join(os.getcwd(), "barsi_quant_ia/credentials.json"),
            ]
            for c in candidates:
                if os.path.exists(c):
                    cred_path = c
                    break

        if cfg.sheets_provider == "gspread" and os.path.exists(cred_path):
            return GSpreadAdapter(
                credentials_path=cred_path,
                spreadsheet_id=cfg.sheets_spreadsheet_id,
                sqlite_db_path=cfg.sqlite_db_path,
            )

        xlsx_path = cfg.local_xlsx_path
        if not os.path.exists(xlsx_path):
            for c in [
                "carteira_barsi_quant_ia.xlsx",
                "barsi_quant_ia/carteira_barsi_quant_ia.xlsx",
                os.path.join(os.path.dirname(__file__), "../../../carteira_barsi_quant_ia.xlsx"),
            ]:
                if os.path.exists(c):
                    xlsx_path = c
                    break

        return XLSXAdapter(file_path=xlsx_path)

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

    @staticmethod
    def create_notification_adapter(cfg: Settings) -> NotificationsPort:
        from barsi.adapters.notifications import (
            TelegramNotifier,
            NoOpNotifier,
            WhatsAppCallMeBotNotifier,
            WhatsAppEvolutionNotifier,
            MultiNotifier,
        )
        notifiers: list[NotificationsPort] = []

        if cfg.telegram_alerts_enabled and cfg.telegram_bot_token and cfg.telegram_chat_id:
            notifiers.append(TelegramNotifier(bot_token=cfg.telegram_bot_token, chat_id=cfg.telegram_chat_id))

        if cfg.whatsapp_alerts_enabled:
            if cfg.whatsapp_provider == "callmebot" and cfg.whatsapp_phone and cfg.whatsapp_apikey:
                notifiers.append(WhatsAppCallMeBotNotifier(phone=cfg.whatsapp_phone, api_key=cfg.whatsapp_apikey))
            elif cfg.whatsapp_provider == "evolution" and cfg.whatsapp_evolution_url and cfg.whatsapp_phone:
                notifiers.append(WhatsAppEvolutionNotifier(
                    base_url=cfg.whatsapp_evolution_url,
                    instance=cfg.whatsapp_evolution_instance,
                    api_key=cfg.whatsapp_apikey,
                    recipient=cfg.whatsapp_phone,
                ))

        if len(notifiers) == 1:
            return notifiers[0]
        elif len(notifiers) > 1:
            return MultiNotifier(notifiers)
        return NoOpNotifier()

