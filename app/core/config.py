import os
from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    APP_NAME: str = "WhatsApp Bot SaaS"
    APP_VERSION: str = "0.1.0"
    ENV: str = os.getenv("APP_ENV", "staging")
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"

    WHATSAPP_VERIFY_TOKEN: str = os.getenv("WHATSAPP_VERIFY_TOKEN", "")
    WHATSAPP_API_TOKEN: str = os.getenv("WHATSAPP_API_TOKEN", "")
    WHATSAPP_PHONE_NUMBER_ID: str = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")

    MONGODB_URI: str = os.getenv("MONGODB_URI", "")
    MONGODB_DB_NAME: str = os.getenv("MONGODB_DB_NAME", "tab_wappbot_ai_stg_db")

    META_API_VERSION: str = "v22.0"
    META_API_BASE_URL: str = "https://graph.facebook.com"

    LLM_API_URL: str = os.getenv("LLM_API_URL", "")
    LLM_API_TIMEOUT: int = int(os.getenv("LLM_API_TIMEOUT", "30"))

    APP_BASE_URL: str = os.getenv("APP_BASE_URL", "")

    @property
    def is_production(self) -> bool:
        return self.ENV == "production"

    @property
    def is_staging(self) -> bool:
        return self.ENV == "staging"

    class Config:
        env_file = ".env"
        case_sensitive = True


@lru_cache()
def get_settings() -> Settings:
    return Settings()
