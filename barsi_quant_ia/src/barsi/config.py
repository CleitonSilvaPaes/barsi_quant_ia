"""Configuração da aplicação via pydantic-settings."""
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field
from typing import Literal
import os

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "barsi_quant_ia/.env", os.path.join(os.path.dirname(__file__), "../../../.env")),
        env_file_encoding="utf-8",
        env_prefix="BARSI_",
        extra="ignore"
    )

    # Runtime
    env: str = "production"
    log_level: str = "INFO"
    api_port: int = 8000
    api_host: str = "0.0.0.0"
    api_bearer_token: str = "barsi_secure_token_default"

    # Restrições de Concorrência e Hardware (TV Box)
    max_concurrency: int = 3
    cache_dir: str = "./cache"
    sqlite_db_path: str = "./memory/barsi_memory.db"

    # Providers
    market_provider: Literal["yfinance", "brapi", "cached"] = "yfinance"
    brapi_token: str = ""

    news_provider: Literal["rss", "stub"] = "rss"

    ai_provider: Literal["rule_engine", "ollama_remote", "openai_compatible"] = "rule_engine"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "gemma2:2b"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"

    # Google Sheets
    sheets_provider: Literal["gspread", "xlsx_fallback"] = "gspread"
    sheets_spreadsheet_id: str = ""
    sheets_credentials_json: str = "./credentials.json"
    local_xlsx_path: str = "carteira_barsi_quant_ia.xlsx"

    # Cloudflare R2 Mirror
    r2_enabled: bool = False
    r2_account_id: str = ""
    r2_access_key_id: str = ""
    r2_secret_access_key: str = ""
    r2_bucket_name: str = "barsi-history"

settings = Settings()

