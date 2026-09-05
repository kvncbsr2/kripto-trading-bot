from datetime import datetime
from typing import Optional

from services.risk_engine.circuit_breaker import CircuitBreaker
from services.risk_engine.position_sizing import calculate_atr_position_size
from services.risk_engine.stop_loss import validate_stop_and_target
from shared.enums import SignalDirection
from shared.logging import get_logger
from shared.schemas import PortfolioState, RiskDecision, Signal

logger = get_logger("risk-engine", service="risk_engine")


class RiskEngine:
    """
    Central Risk Engine V2.
    Enforces $5,000 portfolio guardrails:
    - Max $25 (0.5%) risk per trade
    - Max $50 daily loss limit (DAILY_RISK_LOCK)
    - Max 2 open positions
    - Soft/Hard daily target handling ($20 - $100)
    - Circuit breaker with NORMAL/WARNING/LOCKED/EMERGENCY
    """

    def __init__(
        self,
        risk_per_trade: float = 0.005,  # 0.5%
        daily_max_loss_usd: float = 50.0,  # $50
        max_open_positions: int = 2,
        min_risk_reward: float = 1.5,
        target_mode: str = "SOFT",  # SOFT or HARD
        daily_target_min: float = 20.0,
        daily_target_max: float = 100.0,
        is_spot_mode: bool = True,
        max_trades_per_day: int = 5,
        max_position_equity_ratio: float = 0.40,
    ):
        self.risk_per_trade = risk_per_trade
        self.daily_max_loss_usd = daily_max_loss_usd
        self.max_open_positions = max_open_positions
        self.min_risk_reward = min_risk_reward
        self.target_mode = target_mode
        self.daily_target_min = daily_target_min
        self.daily_target_max = daily_target_max
        self.is_spot_mode = is_spot_mode
        self.max_trades_per_day = max_trades_per_day
        self.max_position_equity_ratio = max_position_equity_ratio

        self.circuit_breaker = CircuitBreaker(
            daily_max_loss_usd=self.daily_max_loss_usd,
            max_drawdown_pct=0.10,
        )

    def evaluate_signal(
        self,
        signal: Signal,
        portfolio: PortfolioState,
        latest_market_time: Optional[datetime] = None,
        current_time: Optional[datetime] = None,
    ) -> RiskDecision:
        from datetime import timezone

        system_now = datetime.now(timezone.utc)
        if current_time is not None:
            now = current_time
        elif latest_market_time is not None:
            lmt = latest_market_time if latest_market_time.tzinfo is not None else latest_market_time.replace(tzinfo=timezone.utc)
            # Detect historical simulation/replay (> 24h in past) vs live trading (< 24h)
            if (system_now - lmt).total_seconds() > 86400.0:
                now = lmt
            else:
                now = system_now
        else:
            now = system_now

        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        # 0. Signal Freshness Validation (Section 10)
        sig_ts = signal.timestamp
        if sig_ts:
            if sig_ts.tzinfo is None:
                sig_ts = sig_ts.replace(tzinfo=timezone.utc)
            sig_age = (now - sig_ts).total_seconds()
            ttl_seconds = 900.0  # 15 minutes TTL for 15m timeframe candles
            if sig_age > ttl_seconds:
                return RiskDecision(
                    approved=False,
                    symbol=signal.symbol,
                    direction=signal.direction,
                    reason=f"Signal expired ({sig_age:.0f}s old > {ttl_seconds:.0f}s TTL)",
                )

        # 0.1 Spot Mode Protection (Requirement 6 & 36)
        if self.is_spot_mode and signal.direction == SignalDirection.SHORT:
            logger.info(
                f"Spot Mode: SHORT signal for {signal.symbol} recorded as SIGNAL_ONLY (execution not supported in Spot)."
            )
            return RiskDecision(
                approved=False,
                symbol=signal.symbol,
                direction=signal.direction,
                reason="Spot Mode: SHORT is NOT_SUPPORTED (SIGNAL_ONLY)",
            )

        # 1. Circuit Breaker & Daily Loss Check with real UTC time (AUDIT-02)
        tripped, reason, event_type = self.circuit_breaker.check(
            portfolio, latest_market_time, current_time=now
        )
        if tripped:
            return RiskDecision(
                approved=False,
                symbol=signal.symbol,
                direction=signal.direction,
                reason=f"Risk Engine Locked: {reason}",
            )

        # 2. Daily Profit Target Policies
        if self.target_mode == "HARD" and portfolio.daily_pnl >= self.daily_target_max:
            return RiskDecision(
                approved=False,
                symbol=signal.symbol,
                direction=signal.direction,
                reason=f"Hard daily profit target reached (+${portfolio.daily_pnl:.2f} >= ${self.daily_target_max:.2f}). Trading halted for the day.",
            )
        elif self.target_mode == "SOFT" and portfolio.daily_pnl >= self.daily_target_min:
            # Under SOFT mode, if minimum target reached, only take exceptional signals (Score >= 75)
            signal_score = signal.metadata.get("score", 60.0)
            if signal_score < 75.0:
                return RiskDecision(
                    approved=False,
                    symbol=signal.symbol,
                    direction=signal.direction,
                    reason=f"Soft daily target active (+${portfolio.daily_pnl:.2f}); rejected signal with score {signal_score} < 75 (High quality filter).",
                )

        # 3. Open Positions Limit (Max 2)
        if len(portfolio.open_positions) >= self.max_open_positions:
            return RiskDecision(
                approved=False,
                symbol=signal.symbol,
                direction=signal.direction,
                reason=f"Max open positions reached ({len(portfolio.open_positions)}/{self.max_open_positions})",
            )

        # 4. Duplicate Symbol Check
        for pos in portfolio.open_positions:
            if pos.symbol == signal.symbol:
                return RiskDecision(
                    approved=False,
                    symbol=signal.symbol,
                    direction=signal.direction,
                    reason=f"Existing position already active for {signal.symbol}",
                )

        # 5. Stop-Loss & Take-Profit R:R Validation
        if not validate_stop_and_target(
            direction=signal.direction,
            entry_price=signal.entry_price,
            stop_price=signal.stop_price,
            take_profit=signal.take_profit,
            min_risk_reward=self.min_risk_reward,
        ):
            return RiskDecision(
                approved=False,
                symbol=signal.symbol,
                direction=signal.direction,
                reason=f"Invalid Stop/Target geometry or R:R below minimum required ({self.min_risk_reward})",
            )

        # 6. Sizing Calculation (e.g. 2% risk = $100 on $5,000 equity)
        size, risk_amount = calculate_atr_position_size(
            equity=portfolio.equity,
            entry_price=signal.entry_price,
            stop_price=signal.stop_price,
            risk_per_trade=self.risk_per_trade,
            max_position_equity_ratio=self.max_position_equity_ratio,
        )

        if size <= 0:
            return RiskDecision(
                approved=False,
                symbol=signal.symbol,
                direction=signal.direction,
                reason="Calculated position size is zero or below minimum allowable threshold",
            )

        logger.info(
            f"Risk APPROVED: {signal.symbol} {signal.direction.value} size={size} "
            f"risk=${risk_amount:.2f} (Portfolio Equity: ${portfolio.equity:.2f})",
            extra={"symbol": signal.symbol, "strategy": signal.strategy},
        )

        return RiskDecision(
            approved=True,
            symbol=signal.symbol,
            direction=signal.direction,
            calculated_size=size,
            entry_price=signal.entry_price,
            stop_loss=signal.stop_price,
            take_profit=signal.take_profit,
            risk_amount=risk_amount,
            reason="All risk parameters satisfied",
        )

    def record_executed_trade(self):
        """Called ONLY upon actual order execution and fill (AUDIT-05)."""
        self.circuit_breaker.record_trade()
        logger.info(
            f"Trade recorded in circuit breaker ({self.circuit_breaker.trades_today_count}/{self.circuit_breaker.max_trades_per_day} trades today)."
        )
