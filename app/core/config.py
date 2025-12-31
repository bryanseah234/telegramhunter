from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # General
    PROJECT_NAME: str = "Telegram Hunter"
    ENV: str = "development"
    DEBUG: bool = True

    # Database & Redis
    SUPABASE_URL: str
    SUPABASE_KEY: str
    REDIS_URL: str

    # Security
    ENCRYPTION_KEY: str  # Fernet Key

    # Telegram Monitoring (The Bot WE control)
    MONITOR_BOT_TOKEN: str
    MONITOR_GROUP_ID: int

    # Telegram Client (For Scraping)
    TELEGRAM_API_ID: int
    TELEGRAM_API_HASH: str

    # OSINT API Keys
    SHODAN_KEY: Optional[str] = None
    FOFA_KEY: Optional[str] = None
    URLSCAN_KEY: Optional[str] = None
    GITHUB_TOKEN: Optional[str] = None

    model_config = SettingsConfigDict(
        env_file=".env", 
        env_file_encoding="utf-8", 
        extra="ignore"
    )

settings = Settings()
