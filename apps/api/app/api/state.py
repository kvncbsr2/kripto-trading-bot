from typing import Any, Dict, Optional

from services.autonomous_runner import autonomous_trader
from services.command_bus.command_bus import CommandBus
from services.market_data.market_data_service import MarketDataService
from services.paper_broker.broker import PaperBroker
from services.paper_broker.validation_engine import PaperValidationEngine
from services.risk_engine.risk_engine import RiskEngine
from shared.config import get_settings
from shared.logging import get_logger

logger = get_logger("api-state", service="api")
settings = get_settings()

# Authoritative Shared Runtime State
RUNTIME_STATE: Dict[str, Any] = {
    "balance": settings.INITIAL_CAPITAL,
    "equity": settings.INITIAL_CAPITAL,
    "daily_pnl": 0.0,
    "unrealized_pnl": 0.0,
    "realized_pnl": 0.0,
    "max_drawdown": 0.0,
    "open_positions": [],
    "closed_positions": [],
    "recent_signals": [],
    "is_halted": False,
    # Initialized as STARTING per Requirement 32
    "system_state": "STARTING",
    "circuit_state": "NORMAL",
    "is_autonomous_active": False,
    "last_cycle_at": None,
    "last_action": "Sistem başlatılıyor (STARTING)...",
}

# Authoritative Services Singletons
risk_engine = RiskEngine(
    risk_per_trade=settings.RISK_PER_TRADE,
    daily_max_loss_usd=settings.DAILY_MAX_LOSS,
    max_open_positions=settings.MAX_OPEN_POSITIONS,
    min_risk_reward=settings.MIN_RISK_REWARD,
    target_mode=settings.TARGET_MODE,
    daily_target_min=settings.DAILY_TARGET_MIN,
    daily_target_max=settings.DAILY_TARGET_MAX,
    is_spot_mode=True,
    max_trades_per_day=settings.MAX_TRADES_PER_DAY,
)

paper_broker = PaperBroker(
    initial_balance=settings.INITIAL_CAPITAL,
    maker_fee=settings.MAKER_FEE,
    taker_fee=settings.TAKER_FEE,
    slippage_bps=settings.SLIPPAGE_BPS,
    is_spot_mode=True,
    db_path="./kripto_agent.db",
)

market_data_service = MarketDataService(symbols=settings.DEFAULT_SYMBOLS)
command_bus = CommandBus(runtime_state=RUNTIME_STATE, broker=paper_broker)
validation_engine = PaperValidationEngine()

# Wire autonomous trader strictly to authoritative bus and market data
autonomous_trader.bind_command_bus(command_bus)
autonomous_trader.bind_market_data_service(market_data_service)
autonomous_trader.risk_engine = risk_engine

LATEST_REPLAY_REPORT: Optional[Dict[str, Any]] = None
EXPERIMENT_RUNS: Dict[str, Dict[str, Any]] = {}
