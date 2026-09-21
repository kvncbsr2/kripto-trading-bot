"""
Execution Engine Factory for KRIPTO AGENT.
Unified factory providing safe instantiation of Paper and Live execution engines.
Enforces strict fail-closed defaults:
- Default mode is always PAPER.
- LIVE mode strictly requires settings.LIVE_TRADING and settings.LIVE_TRADING_ARMED to be True.
- Provides startup state reconciliation against persistent SQLite ledger.
"""

from enum import Enum
from typing import Any, Dict, Optional

from services.execution.execution_interface import ExecutionEngine
from services.paper_broker.broker import PaperBroker
from shared.config import get_settings
from shared.logging import get_logger

logger = get_logger("execution-factory", service="execution")
settings = get_settings()


class ExecutionMode(str, Enum):
    PAPER = "PAPER"
    LIVE = "LIVE"


class ExecutionEngineFactory:
    """
    Factory creating execution engines with strict safety invariants.
    """

    @staticmethod
    def create_execution_engine(
        mode: Optional[str] = None,
        db_path: Optional[str] = None,
        **kwargs: Any,
    ) -> ExecutionEngine:
        target_mode = (mode or getattr(settings, "TRADING_MODE", "PAPER")).upper()

        if target_mode == ExecutionMode.LIVE.value:
            # Enforce Two-Step Activation Guardrails
            if not getattr(settings, "LIVE_TRADING", False):
                logger.critical("EXECUTION FACTORY: LIVE mode requested but settings.LIVE_TRADING is False!")
                raise RuntimeError("FAIL_CLOSED: Live trading is not enabled in settings.")

            if not getattr(settings, "LIVE_TRADING_ARMED", False):
                logger.critical("EXECUTION FACTORY: LIVE mode requested but settings.LIVE_TRADING_ARMED is False!")
                raise RuntimeError("FAIL_CLOSED: Live trading arming switch is not armed.")

            from services.execution.live_binance_execution import BinanceLiveExecutionEngine
            logger.warning("EXECUTION FACTORY: Initializing BinanceLiveExecutionEngine in LIVE mode.")
            return BinanceLiveExecutionEngine(db_path=db_path, **kwargs)

        # Default fallback is always PaperBroker
        logger.info(f"EXECUTION FACTORY: Initializing PaperBroker (mode={target_mode}).")
        paper_kwargs = {
            "initial_balance": kwargs.get("initial_balance", settings.INITIAL_CAPITAL),
            "maker_fee": kwargs.get("maker_fee", settings.MAKER_FEE),
            "taker_fee": kwargs.get("taker_fee", settings.TAKER_FEE),
            "slippage_bps": kwargs.get("slippage_bps", settings.SLIPPAGE_BPS),
            "is_spot_mode": kwargs.get("is_spot_mode", True),
            "db_path": db_path or str(getattr(settings, "DATABASE_PATH", "kripto_agent.db")),
        }
        return PaperBroker(**paper_kwargs)
