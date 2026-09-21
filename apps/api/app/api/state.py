from pathlib import Path
from typing import Any, Dict, Optional

from services.autonomous_runner import autonomous_trader
from services.command_bus.command_bus import CommandBus
from services.market_data.market_data_service import MarketDataService
from services.paper_broker.broker import PaperBroker
from services.paper_broker.validation_engine import PaperValidationEngine
from services.risk_engine.risk_engine import RiskEngine
from shared.config import get_settings
from shared.logging import get_logger

PROJECT_ROOT = Path(__file__).resolve().parents[4]

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
    "max_open_positions": settings.MAX_OPEN_POSITIONS,
    "max_open_positions_override": None,
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
    max_position_equity_ratio=getattr(settings, "MAX_POSITION_EQUITY_RATIO", 0.40),
)
# Circuit breaker starts ACTIVE by default (no unconditional suspension)
# Hard ceiling invariant: max_open_positions cannot exceed 8
risk_engine.max_open_positions_override = None

from services.execution.factory import ExecutionEngineFactory
from services.execution.reconciliation import ReconciliationEngine

paper_broker = ExecutionEngineFactory.create_execution_engine(
    mode=getattr(settings, "TRADING_MODE", "PAPER"),
    db_path=str(PROJECT_ROOT / "kripto_agent.db"),
)

# Run startup reconciliation check
try:
    import asyncio
    reconciler = ReconciliationEngine(execution_engine=paper_broker)
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            reconciliation_report = pool.submit(
                asyncio.run,
                reconciler.reconcile_orders_and_positions(
                    local_open_positions=paper_broker.open_positions,
                    local_orders=paper_broker.orders,
                )
            ).result()
    else:
        reconciliation_report = asyncio.run(reconciler.reconcile_orders_and_positions(
            local_open_positions=paper_broker.open_positions,
            local_orders=paper_broker.orders,
        ))
        if reconciliation_report.is_synchronized:
            logger.info("Startup state reconciliation PASSED: in-memory broker and persistent ledger are synchronized.")
        else:
            logger.critical(
                f"RECONCILIATION FAILURE: {len(reconciliation_report.discrepancies)} state discrepancies detected on startup!"
            )
            RUNTIME_STATE["circuit_breaker_tripped"] = True
            RUNTIME_STATE["trading_halted"] = True
            RUNTIME_STATE["reconciliation_required"] = True
            RUNTIME_STATE["reconciliation_discrepancies"] = [
                d.model_dump() if hasattr(d, "model_dump") else d.dict() for d in reconciliation_report.discrepancies
            ]
            if getattr(settings, "LIVE_TRADING", False) or getattr(settings, "TRADING_MODE", "PAPER").upper() == "LIVE":
                raise RuntimeError(
                    f"FAIL-CLOSED INVARIANT: Live trading boot blocked due to {len(reconciliation_report.discrepancies)} state discrepancies!"
                )
except Exception as _rec_err:
    if getattr(settings, "LIVE_TRADING", False) or getattr(settings, "TRADING_MODE", "PAPER").upper() == "LIVE":
        logger.critical(f"FATAL: Startup reconciliation crashed in LIVE mode: {_rec_err}")
        raise
    logger.warning(f"Startup reconciliation warning: {_rec_err}")

market_data_service = MarketDataService(symbols=settings.DEFAULT_SYMBOLS)
command_bus = CommandBus(runtime_state=RUNTIME_STATE, broker=paper_broker)
validation_engine = PaperValidationEngine()

# Wire autonomous trader strictly to authoritative bus and market data
autonomous_trader.bind_command_bus(command_bus)
autonomous_trader.bind_market_data_service(market_data_service)
autonomous_trader.risk_engine = risk_engine

# Synchronize authoritative initial risk profile and strategy across runtime and engines
from services.config_manager.risk_profiles import (
    apply_profile_to_system,
    apply_strategy_to_system,
    load_persisted_profile_state,
)

try:
    # Enforce Level 1 (Conservative) on startup unconditionally
    apply_profile_to_system(1, source="startup_enforced_l1")
    persisted = load_persisted_profile_state()
    initial_strategy = persisted.get("active_strategy_id", "momentum_dip_rebound")
    apply_strategy_to_system(initial_strategy, source="startup")
    # Startup Circuit Breaker & Daily PnL Synchronization
    if paper_broker:
        port_state = paper_broker.get_portfolio_state()
        daily_pnl = getattr(port_state, "daily_pnl", 0.0)
        max_loss = getattr(risk_engine, "daily_max_loss_usd", 50.0)
        if daily_pnl <= -max_loss:
            from services.risk_engine.circuit_breaker import CircuitState
            risk_engine.circuit_breaker.state = CircuitState.LOCKED
            risk_engine.circuit_breaker.trip_reason = f"DAILY_RISK_LOCK: Daily loss reached -${abs(daily_pnl):.2f} (limit: -${max_loss:.2f})"
            RUNTIME_STATE["circuit_state"] = CircuitState.LOCKED.value
            RUNTIME_STATE["is_halted"] = True
            RUNTIME_STATE["system_state"] = "HALTED"
            logger.critical(f"STARTUP CIRCUIT BREAKER LOCKED: Daily loss limit breached on startup (${daily_pnl:.2f} <= -${max_loss:.2f}). System is HALTED.")
except Exception as e:
    logger.critical(f"FATAL: Failed to apply initial Risk Profile or Strategy on startup: {e}")
    raise RuntimeError(f"Cannot initialize system risk profile or strategy on startup: {e}") from e

LATEST_REPLAY_REPORT: Optional[Dict[str, Any]] = None
EXPERIMENT_RUNS: Dict[str, Dict[str, Any]] = {}
