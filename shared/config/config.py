from functools import lru_cache
from typing import Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    APP_ENV: str = "development"
    LOG_LEVEL: str = "INFO"

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./kripto_agent.db"
    SYNC_DATABASE_URL: str = "sqlite:///./kripto_agent.db"
    REDIS_URL: str = "redis://localhost:6379/0"

    # Exchange (Read-Only)
    EXCHANGE_NAME: str = "binance"
    EXCHANGE_API_KEY: Optional[str] = None
    EXCHANGE_API_SECRET: Optional[str] = None
    EXCHANGE_TESTNET: bool = False

    # STRICT LIVE TRADING GUARDRAILS
    PAPER_TRADING: bool = True
    LIVE_TRADING: bool = False  # Hard guardrail

    # V2 7-Day $5,000 Experiment Configuration
    EXPERIMENT_NAME: str = "7_DAY_5000_PAPER_TEST"
    INITIAL_CAPITAL: float = 5000.0
    BASE_CURRENCY: str = "USDT"

    # Risk Management
    RISK_PER_TRADE: float = 0.005  # 0.5% -> $25 per trade
    DAILY_MAX_LOSS: float = 50.0  # $50 max loss per day
    DAILY_TARGET_MIN: float = 20.0
    DAILY_TARGET_MAX: float = 100.0
    TARGET_MODE: str = "SOFT"  # SOFT or HARD
    MAX_TRADES_PER_DAY: int = 5
    MAX_OPEN_POSITIONS: int = 2
    LEVERAGE: float = 1.0

    # Execution & Cost Simulation
    MAKER_FEE: float = 0.001  # 0.1%
    TAKER_FEE: float = 0.001  # 0.1%
    SLIPPAGE_BPS: float = 5.0  # 5 bps

    # Strategy & Signal Tuning
    ATR_SL_MULTIPLIER: float = 1.5
    MIN_RISK_REWARD: float = 1.5
    PREFERRED_RISK_REWARD: float = 2.0
    MIN_SIGNAL_SCORE: float = 55.0
    MIN_OPPORTUNITY_SCORE: float = 50.0

    # Telegram
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    TELEGRAM_CHAT_ID: Optional[str] = None
    TELEGRAM_NOTIFICATIONS_ENABLED: bool = False

    # API Server
    API_HOST: str = "0.0.0.0"
    API_PORT: int = 8000


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    # Enforce strict safety assertion: LIVE_TRADING MUST NOT be active in V2
    if settings.LIVE_TRADING:
        raise RuntimeError(
            "CRITICAL SECURITY VIOLATION: LIVE TRADING IS DISABLED DURING VALIDATION."
        )
    return settings
