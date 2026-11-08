"""Configuração da aplicação via pydantic-settings."""
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, field_validator
from typing import Literal, Union, Any, List
import os

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "barsi_quant_ia/.env", os.path.join(os.path.dirname(__file__), "../../../.env")),
        env_file_encoding="utf-8",
        env_prefix="BARSI_",
        extra="ignore"
    )

    # Runtime & Segurança (Zero senhas em código - carregamento 100% via .env ou banco)
    env: str = "production"
    log_level: str = "INFO"
    api_port: int = 8000
    api_host: str = "0.0.0.0"
    api_bearer_token: str = Field(default_factory=lambda: os.getenv("BARSI_API_BEARER_TOKEN", ""))
    secret_key: str = Field(default_factory=lambda: os.getenv("BARSI_SECRET_KEY", "barsi_hmac_secret_key_secure_2026"))
    initial_admin_user: str = Field(default_factory=lambda: os.getenv("BARSI_INITIAL_ADMIN_USER", ""))
    initial_admin_pass: str = Field(default_factory=lambda: os.getenv("BARSI_INITIAL_ADMIN_PASS", ""))
    titular_nomes: Union[List[str], str] = Field(default_factory=list)
    familiar_keywords: Union[List[str], str] = Field(default_factory=list)

    @field_validator("titular_nomes", "familiar_keywords", mode="after")
    @classmethod
    def _parse_list_or_str(cls, v: Any) -> List[str]:
        if isinstance(v, str):
            return [x.strip().upper() for x in v.split(",") if x.strip()]
        if isinstance(v, (list, tuple)):
            return [str(x).strip().upper() for x in v if str(x).strip()]
        return []

    # Conexão TV Box SSH (Zero credenciais hardcoded)
    tvbox_ip: str = Field(default_factory=lambda: os.getenv("BARSI_TVBOX_IP", ""))
    tvbox_user: str = Field(default_factory=lambda: os.getenv("BARSI_TVBOX_USER", ""))
    tvbox_pass: str = Field(default_factory=lambda: os.getenv("BARSI_TVBOX_PASS", ""))
    tvbox_home: str = Field(default_factory=lambda: os.getenv("BARSI_TVBOX_HOME", "/home/cleiton/barsi_quant_ia"))

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

    # Alertas Proativos (Telegram)
    telegram_alerts_enabled: bool = False
    telegram_bot_token: str = Field(default_factory=lambda: os.getenv("BARSI_TELEGRAM_BOT_TOKEN", ""))
    telegram_chat_id: str = Field(default_factory=lambda: os.getenv("BARSI_TELEGRAM_CHAT_ID", ""))

    # Alertas Proativos (WhatsApp)
    whatsapp_alerts_enabled: bool = False
    whatsapp_provider: Literal["callmebot", "evolution"] = "callmebot"
    whatsapp_phone: str = Field(default_factory=lambda: os.getenv("BARSI_WHATSAPP_PHONE", ""))
    whatsapp_apikey: str = Field(default_factory=lambda: os.getenv("BARSI_WHATSAPP_APIKEY", ""))
    whatsapp_evolution_url: str = Field(default_factory=lambda: os.getenv("BARSI_WHATSAPP_EVOLUTION_URL", "http://localhost:8080"))
    whatsapp_evolution_instance: str = Field(default_factory=lambda: os.getenv("BARSI_WHATSAPP_EVOLUTION_INSTANCE", "barsi"))

settings = Settings()

