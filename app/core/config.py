import os
from pydantic_settings import BaseSettings
from functools import lru_cache


def _get_env_file():
    debug = os.getenv("DEBUG", "DEV").upper()
    if debug == "FALSE":
        return "env-prod"
    return "env-stage"


class Settings(BaseSettings):
    APP_NAME: str = "WhatsApp Bot SaaS"
    APP_VERSION: str = "0.1.0"
    APP_ENV: str = "staging"
    DEBUG: str = os.getenv("DEBUG", "DEV")

    WHATSAPP_VERIFY_TOKEN: str = ""
    WHATSAPP_API_TOKEN: str = ""
    WHATSAPP_PHONE_NUMBER_ID: str = ""
    WHATSAPP_API_URL: str = ""
    WHATSAPP_API_BASE_URL: str = "https://graph.facebook.com/v22.0"

    MONGODB_URI: str = ""
    MONGODB_DB_NAME: str = "tab_wappbot_ai_stg_db"

    META_API_VERSION: str = "v22.0"
    META_API_BASE_URL: str = "https://graph.facebook.com"

    LLM_API_URL: str = ""
    LLM_API_TIMEOUT: int = 120

    APP_BASE_URL: str = ""

    IPURVEY_BASE_URL: str = "https://dev-ilekun-ipv.ipurvey.com"
    IPURVEY_JWT_TOKEN: str = ""

    ADMIN_SECRET: str = ""

    @property
    def ENV(self) -> str:
        return self.APP_ENV

    @property
    def is_production(self) -> bool:
        return self.ENV == "production"

    @property
    def is_staging(self) -> bool:
        return self.ENV == "staging"

    class Config:
        env_file = _get_env_file()
        case_sensitive = True

@lru_cache()
def get_settings() -> Settings:
    return Settings()
