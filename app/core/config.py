import os
from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # General
    PROJECT_NAME: str = "Telegram Hunter"
    ENV: str = "development"
    DEBUG: bool = True

    # Database & Redis
    SUPABASE_URL: str
    SUPABASE_KEY: str  # Anon key (for frontend)
    SUPABASE_SERVICE_ROLE_KEY: str  # Service role key (bypasses RLS)
    REDIS_URL: str

    # Security
    ENCRYPTION_KEY: str  # Fernet Key

    # Telegram Monitoring (The Bot WE control)
    MONITOR_BOT_TOKEN: str
    MONITOR_GROUP_ID: int | str
    WHITELISTED_BOT_IDS: str = "" # Comma-separated IDs (or usernames)


    # Telegram Client (For Scraping)
    TELEGRAM_API_ID: int
    TELEGRAM_API_HASH: str

    # User Agent Session (Base64 encoded for Railway)
    USER_SESSION_STRING: Optional[str] = None
    
    # OSINT KeysAPI Keys
    SHODAN_KEY: Optional[str] = None
    FOFA_EMAIL: Optional[str] = None
    FOFA_KEY: Optional[str] = None
    URLSCAN_KEY: Optional[str] = None
    GITHUB_TOKEN: Optional[str] = None
    CENSYS_ID: Optional[str] = None
    CENSYS_SECRET: Optional[str] = None
    HYBRID_ANALYSIS_KEY: Optional[str] = None
    
    # Target Countries (High Volume Telegram Usage)
    # RU: Russia, IR: Iran, IN: India, ID: Indonesia, BR: Brazil, UA: Ukraine
    # VN: Vietnam, US: USA, NG: Nigeria, EG: Egypt, KB: Kazakhstan (KZ)
    TARGET_COUNTRIES: list[str] = ["RU", "IR", "IN", "ID", "BR", "UA", "VN", "US", "NG", "EG", "KZ", "CN", "DE"]

    model_config = SettingsConfigDict(
        env_file=os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".env"),
        env_file_encoding="utf-8", 
        extra="ignore"
    )

settings = Settings()
