from datetime import datetime, timezone
from enum import Enum
from typing import Optional, Tuple

from shared.enums import RiskEventType
from shared.logging import get_logger
from shared.schemas import PortfolioState

logger = get_logger("circuit-breaker", service="risk_engine")


class CircuitState(str, Enum):
    NORMAL = "NORMAL"
    WARNING = "WARNING"
    LOCKED = "LOCKED"
    EMERGENCY = "EMERGENCY"


class CircuitBreaker:
    def __init__(
        self,
        daily_max_loss_usd: float = 50.0,
        max_drawdown_pct: float = 0.10,  # 10%
        stale_data_seconds: int = 180,  # 3 minutes
        max_trades_per_day: int = 5,
    ):
        self.daily_max_loss_usd = daily_max_loss_usd
        self.max_drawdown_pct = max_drawdown_pct
        self.stale_data_seconds = stale_data_seconds
        self.max_trades_per_day = max_trades_per_day

        self.state: CircuitState = CircuitState.NORMAL
        self.trip_reason: Optional[str] = None
        self.trades_today_count: int = 0
        self.last_day_reset: Optional[datetime] = None

    def record_trade(self):
        self.trades_today_count += 1
        if self.trades_today_count >= self.max_trades_per_day:
            self.state = CircuitState.LOCKED
            self.trip_reason = (
                f"Max daily trades reached ({self.trades_today_count}/{self.max_trades_per_day})"
            )

    def check(
        self,
        portfolio: PortfolioState,
        latest_candle_time: Optional[datetime] = None,
        current_time: Optional[datetime] = None,
    ) -> Tuple[bool, Optional[str], Optional[RiskEventType]]:
        """
        Evaluates system limits. Returns (should_halt, reason, event_type).
        """
        if self.state in [CircuitState.LOCKED, CircuitState.EMERGENCY]:
            return True, self.trip_reason, RiskEventType.CIRCUIT_BREAKER_TRIGGERED

        # 1. Daily Loss Check in USD ($50 max loss)
        if portfolio.daily_pnl <= -self.daily_max_loss_usd:
            self.state = CircuitState.LOCKED
            self.trip_reason = f"DAILY_RISK_LOCK: Daily loss reached -${abs(portfolio.daily_pnl):.2f} (limit: -${self.daily_max_loss_usd:.2f})"
            logger.critical(self.trip_reason)
            return True, self.trip_reason, RiskEventType.DAILY_LOSS_EXCEEDED

        # 2. Maximum Drawdown Check (10%)
        if portfolio.max_drawdown_current >= self.max_drawdown_pct:
            self.state = CircuitState.EMERGENCY
            self.trip_reason = f"EMERGENCY: Maximum drawdown breached ({portfolio.max_drawdown_current * 100:.2f}% >= {self.max_drawdown_pct * 100:.1f}%)"
            logger.critical(self.trip_reason)
            return True, self.trip_reason, RiskEventType.MAX_DRAWDOWN_EXCEEDED

        # 3. Market Data Freshness / Stale Data Check
        if latest_candle_time is not None:
            now = current_time or datetime.now(timezone.utc)
            if now.tzinfo is None:
                now = now.replace(tzinfo=timezone.utc)
            candle_time = latest_candle_time
            if candle_time.tzinfo is None:
                candle_time = candle_time.replace(tzinfo=timezone.utc)

            diff = (now - candle_time).total_seconds()
            if diff > self.stale_data_seconds:
                self.state = CircuitState.WARNING
                reason = f"Market data feed is stale ({diff:.0f}s since last candle, threshold: {self.stale_data_seconds}s)"
                logger.warning(reason)
                return True, reason, RiskEventType.STALE_DATA_DETECTED

        return False, None, None

    def reset(self):
        self.state = CircuitState.NORMAL
        self.trip_reason = None
        logger.info("Circuit breaker manually reset to NORMAL.")
