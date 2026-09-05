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

    # Exchange & Network Environment
    EXCHANGE_NAME: str = "binance"
    EXCHANGE_API_KEY: Optional[str] = None
    EXCHANGE_API_SECRET: Optional[str] = None
    EXCHANGE_TESTNET: bool = False
    BINANCE_ENV: str = "testnet"  # "testnet" or "production_market_data"
    BINANCE_API_KEY: Optional[str] = None
    BINANCE_API_SECRET: Optional[str] = None
    BINANCE_TESTNET_REST_URL: str = "https://testnet.binance.vision/api"
    BINANCE_TESTNET_WS_URL: str = "wss://testnet.binance.vision/ws"

    # DECOUPLED DATA / EXECUTION MODES
    MARKET_DATA_SOURCE: str = "binance"  # Real Binance Live Data
    EXECUTION_MODE: str = "paper"        # Strict Virtual Execution
    SECURITY_LEVEL: int = 1             # Level 1: Market Data + Paper Execution

    # STRICT LIVE TRADING GUARDRAILS (Section 25 & 26)
    PAPER_TRADING: bool = True
    LIVE_TRADING: bool = False  # Hard guardrail (default False)
    LIVE_TRADING_ARMED: bool = False  # Step 2 of two-step activation

    # API Security & Authentication (Section 27)
    API_KEY_AUTH_ENABLED: bool = False  # Enable in production
    API_AUTH_SECRET: str = "kripto-agent-secret-token"
    API_ADMIN_KEY: str = "kripto-agent-admin-key"

    # R10 RSI Divergence Configuration
    R10_ENABLED: bool = True
    R10_TIMEFRAME: str = "15m"
    R10_RSI_LENGTH: int = 14
    R10_PIVOT_LEFT: int = 5
    R10_PIVOT_RIGHT: int = 5
    R10_CONFIRMATION_ENABLED: bool = True

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
    MAX_SPREAD_BPS: float = 15.0  # Max spread 15 bps
    MIN_24H_VOLUME_USDT: float = 10000000.0  # Min $10M 24h volume
    BINANCE_ENVIRONMENT: str = "production_market_data"

    # Strategy & Signal Tuning
    ATR_SL_MULTIPLIER: float = 1.5
    MIN_RISK_REWARD: float = 1.5
    PREFERRED_RISK_REWARD: float = 2.0
    MIN_SIGNAL_SCORE: float = 70.0
    MIN_OPPORTUNITY_SCORE: float = 50.0

    # Market Universe
    DEFAULT_SYMBOLS: list[str] = [
        "BTC/USDT",
        "ETH/USDT",
        "BNB/USDT",
        "SOL/USDT",
        "XRP/USDT",
        "DOGE/USDT",
        "ADA/USDT",
        "AVAX/USDT",
        "LINK/USDT",
    ]
    TIMEFRAMES: list[str] = ["1m", "5m", "15m", "1h", "4h", "1d"]

    # Telegram
    TELEGRAM_BOT_TOKEN: Optional[str] = None
    TELEGRAM_CHAT_ID: Optional[str] = None
    TELEGRAM_NOTIFICATIONS_ENABLED: bool = False

    # Bitcoin Trend Shield (Regime Filter)
    BTC_REGIME_FILTER_ENABLED: bool = True
    BTC_DUMP_THRESHOLD_PCT: float = -1.5  # If BTC 15m/1h returns fall below -1.5%, suppress altcoin longs

    # API Server
    API_HOST: str = "127.0.0.1"
    API_PORT: int = 8000


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    # Enforce two-step live trading activation safety guardrail (AUDIT-13)
    if settings.LIVE_TRADING and not settings.LIVE_TRADING_ARMED:
        raise RuntimeError(
            "CRITICAL SECURITY VIOLATION: LIVE_TRADING is True but LIVE_TRADING_ARMED is False (Two-step activation required)."
        )
    return settings
