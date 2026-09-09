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
        self.baseline_trade_count: int = 0

    def _rollover_utc_day(self, current_time: Optional[datetime] = None):
        now = current_time or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        if self.last_day_reset is None:
            self.last_day_reset = now
            return
        if self.last_day_reset.astimezone(timezone.utc).date() != now.astimezone(timezone.utc).date():
            self.trades_today_count = 0
            self.state = CircuitState.NORMAL
            self.trip_reason = None
            self.last_day_reset = now

    def record_trade(self, current_time: Optional[datetime] = None):
        self._rollover_utc_day(current_time)
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
        now = current_time or datetime.now(timezone.utc)
        self._rollover_utc_day(now)
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

    def check_expectancy(self, closed_positions: list) -> Tuple[bool, Optional[str], Optional[RiskEventType]]:
        """
        Evaluates mathematical expectancy over closed positions.
        Rule: If N >= 5 and Expectancy <= 0, halt immediately.
        """
        if self.state in [CircuitState.LOCKED, CircuitState.EMERGENCY]:
            return True, self.trip_reason, RiskEventType.CIRCUIT_BREAKER_TRIGGERED

        # Only evaluate trades since the baseline trade count (e.g. after a manual reset/unhalt)
        eval_trades = closed_positions[self.baseline_trade_count:] if self.baseline_trade_count > 0 else closed_positions

        from services.risk_engine.expectancy_engine import ExpectancyEngine
        metrics = ExpectancyEngine.calculate_from_positions(eval_trades)
        if metrics.get("should_halt", False):
            self.state = CircuitState.LOCKED
            exp_usd = metrics['expectancy_usd_per_trade']
            exp_str = f"-${abs(exp_usd):.2f}" if exp_usd < 0 else f"${exp_usd:.2f}"
            self.trip_reason = (
                f"EXPECTANCY_HALT: Mathematical Expectancy turned negative ({exp_str} <= 0). "
                f"WinRate %{metrics['win_rate_pct']:.1f} fell below breakeven %{metrics['breakeven_win_rate_pct']:.1f}. Trading halted."
            )
            logger.critical(self.trip_reason)
            return True, self.trip_reason, RiskEventType.CIRCUIT_BREAKER_TRIGGERED

        return False, None, None

    def reset(self, baseline_trade_count: Optional[int] = None):
        self.state = CircuitState.NORMAL
        self.trip_reason = None
        if baseline_trade_count is not None:
            self.baseline_trade_count = baseline_trade_count
        logger.info(f"Circuit breaker manually reset to NORMAL (baseline_trade_count={self.baseline_trade_count}).")
