import os
from typing import Optional
from pydantic import field_validator, model_validator, ValidationError
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

    # Telegram Monitoring (The Bot(s) WE control - supports multi-bot rotation)
    # Comma-separated bot tokens, e.g. "token1,token2,token3"
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
    GITLAB_TOKEN: Optional[str] = None
    BITBUCKET_USER: Optional[str] = None
    BITBUCKET_APP_PASSWORD: Optional[str] = None
    PUBLICWWW_KEY: Optional[str] = None
    SERPER_API_KEY: Optional[str] = None
    CENSYS_ID: Optional[str] = None
    CENSYS_SECRET: Optional[str] = None
    HYBRID_ANALYSIS_KEY: Optional[str] = None
    GOOGLE_SEARCH_KEY: Optional[str] = None
    GOOGLE_CSE_ID: Optional[str] = None
    
    # Target Countries (Tiered by Telegram usage volume)
    # Primary:   Top Telegram DAU per capita (CIS, South/Southeast Asia, MENA, LatAm)
    # Secondary: Large tech populations with significant Telegram usage
    # Tertiary:  Emerging Telegram markets and growing adoption regions
    TARGET_COUNTRIES: list[str] = [
        # Primary — Highest Telegram penetration
        "RU", "IR", "IN", "ID", "BR", "UA", "UZ", "KZ", "BY",
        # Secondary — High volume tech populations
        "US", "DE", "GB", "FR", "ES", "IT", "TR", "EG", "NG",
        "PK", "BD", "PH", "VN", "TH", "MY", "CN", "KR", "JP",
        # Tertiary — Emerging markets
        "AZ", "GE", "TJ", "KG", "MD", "AM", "SA", "AE", "IQ",
        "CO", "MX", "AR", "PE", "RO", "PL", "CZ", "NL", "SE", "FI",
    ]

    # Parsed bot tokens list (computed from MONITOR_BOT_TOKEN)
    _bot_tokens: list[str] = []

    @property
    def bot_tokens(self) -> list[str]:
        """Returns parsed list of bot tokens from MONITOR_BOT_TOKEN."""
        return self._bot_tokens

    @model_validator(mode='after')
    def parse_bot_tokens(self) -> 'Settings':
        """Parse comma-separated MONITOR_BOT_TOKEN into a validated list."""
        raw = self.MONITOR_BOT_TOKEN
        if isinstance(raw, str):
            tokens = [t.strip() for t in raw.split(',') if t.strip()]
        else:
            tokens = [raw] if raw else []
            
        if not tokens:
            raise ValueError('MONITOR_BOT_TOKEN cannot be empty')
            
        for token in tokens:
            if ':' not in token or not token.split(':')[0].isdigit():
                raise ValueError(f'Invalid bot token format: {token}')
        
        object.__setattr__(self, '_bot_tokens', tokens)
        return self

    @field_validator('SUPABASE_URL')
    @classmethod
    def validate_supabase_url(cls, v: str) -> str:
        if not v.startswith('https://') and not v.startswith('http://'):
            raise ValueError('SUPABASE_URL must start with https:// or http://')
        return v
    
    @field_validator('REDIS_URL')
    @classmethod
    def validate_redis_url(cls, v: str) -> str:
        if not v.startswith('redis://') and not v.startswith('rediss://'):
            raise ValueError('REDIS_URL must start with redis:// or rediss://')
        return v
    
    @field_validator('ENCRYPTION_KEY')
    @classmethod
    def validate_encryption_key(cls, v: str) -> str:
        # Fernet keys are 44 characters (32 bytes base64 encoded)
        if len(v) != 44:
            raise ValueError('ENCRYPTION_KEY must be 44 characters (Fernet key)')
        return v

    model_config = SettingsConfigDict(
        env_file=os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), ".env"),
        env_file_encoding="utf-8", 
        extra="ignore"
    )

settings = Settings()

