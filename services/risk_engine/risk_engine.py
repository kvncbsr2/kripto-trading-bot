from datetime import datetime
from typing import Optional

from services.risk_engine.circuit_breaker import CircuitBreaker
from services.risk_engine.position_sizing import calculate_atr_position_size
from services.risk_engine.stop_loss import validate_stop_and_target
from shared.config import get_settings
from shared.enums import PositionSide, SignalDirection
from shared.logging import get_logger
from shared.schemas import PortfolioState, RiskDecision, Signal

logger = get_logger("risk-engine", service="risk_engine")
settings = get_settings()


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
        target_mode: str = "HARD",  # SOFT or HARD
        daily_target_min: float = 35.0,
        daily_target_max: float = 50.0,
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
            max_trades_per_day=self.max_trades_per_day,
        )

    def evaluate_signal(
        self,
        signal: Signal,
        portfolio: PortfolioState,
        latest_market_time: Optional[datetime] = None,
        current_time: Optional[datetime] = None,
        closed_positions: Optional[list] = None,
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
            tf = (signal.metadata.get("timeframe") if signal.metadata else None) or getattr(settings, "R10_TIMEFRAME", "15m")
            seconds_per_tf = {
                "1m": 60.0, "3m": 180.0, "5m": 300.0, "15m": 900.0, "30m": 1800.0,
                "1h": 3600.0, "2h": 7200.0, "4h": 14400.0, "6h": 21600.0, "8h": 28800.0,
                "12h": 43200.0, "1d": 86400.0, "3d": 259200.0, "1w": 604800.0,
            }
            ttl_seconds = seconds_per_tf.get(str(tf).lower(), 900.0)
            if sig_age > ttl_seconds:
                return RiskDecision(
                    approved=False,
                    symbol=signal.symbol,
                    direction=signal.direction,
                    reason=f"Signal expired ({sig_age:.0f}s old > {ttl_seconds:.0f}s TTL for {tf})",
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

        # 0.2 Opportunity Score Hard Gate (P1-005)
        min_opp_score = getattr(settings, "MIN_OPPORTUNITY_SCORE", 50.0)
        opp_score = getattr(signal, "opportunity_score", None)
        if opp_score is None and signal.metadata:
            opp_score = signal.metadata.get("opportunity_score")
        if opp_score is not None and opp_score < min_opp_score:
            logger.info(
                f"Opportunity Score Gate: Rejected {signal.symbol} signal. Score {opp_score:.1f} < min {min_opp_score:.1f}"
            )
            return RiskDecision(
                approved=False,
                symbol=signal.symbol,
                direction=signal.direction,
                reason=f"REJECTED_LOW_OPPORTUNITY_SCORE: Opportunity score {opp_score:.1f} < {min_opp_score:.1f}",
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

        # 1.1 Mathematical Expectancy Check (Rule: Expectancy <= 0 on N>=5 halts trading)
        if closed_positions:
            exp_tripped, exp_reason, _ = self.circuit_breaker.check_expectancy(closed_positions)
            if exp_tripped:
                return RiskDecision(
                    approved=False,
                    symbol=signal.symbol,
                    direction=signal.direction,
                    reason=f"Risk Engine Locked (Mathematical Expectancy): {exp_reason}",
                )

        # 2. Daily Profit Target Policies
        daily_profit = portfolio.daily_realized_pnl
        if daily_profit is None:
            daily_profit = portfolio.daily_pnl - portfolio.unrealized_pnl
        if self.target_mode == "HARD" and daily_profit >= self.daily_target_max:
            return RiskDecision(
                approved=False,
                symbol=signal.symbol,
                direction=signal.direction,
                reason=f"Hard daily profit target reached (+${daily_profit:.2f} realized >= ${self.daily_target_max:.2f}). New entries halted.",
            )
        elif self.target_mode == "SOFT" and daily_profit >= self.daily_target_min:
            # Under SOFT mode, if minimum target reached, only take exceptional signals (Score >= 75)
            signal_score = signal.metadata.get("score", 60.0)
            if signal_score < 75.0:
                return RiskDecision(
                    approved=False,
                    symbol=signal.symbol,
                    direction=signal.direction,
                    reason=f"Soft daily target active (+${daily_profit:.2f}); rejected signal with score {signal_score} < 75 (High quality filter).",
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

        # Conservative round-trip cost budget, including stop/target exit costs.
        # Maker fills are not guaranteed, so use taker costs for admission.
        fee_rate = max(settings.TAKER_FEE, settings.MAKER_FEE)
        slippage_bps = settings.SLIPPAGE_BPS
        slip_rate = slippage_bps / 10000.0
        friction = fee_rate + slip_rate + fee_rate * slip_rate
        unit_risk = abs(signal.entry_price - signal.stop_price) + (
            signal.entry_price + signal.stop_price
        ) * friction
        net_reward = abs(signal.take_profit - signal.entry_price) - (
            signal.entry_price + signal.take_profit
        ) * friction
        if net_reward < unit_risk * settings.MIN_NET_RISK_REWARD:
            return RiskDecision(
                approved=False,
                symbol=signal.symbol,
                direction=signal.direction,
                reason="NET_RISK_REWARD: insufficient reward after fees and slippage",
            )

        # Reserve remaining downside of open positions before adding new risk.
        open_risk = 0.0
        for pos in portfolio.open_positions:
            if pos.stop_loss is None or pos.stop_loss <= 0:
                return RiskDecision(
                    approved=False, symbol=signal.symbol, direction=signal.direction,
                    reason="DAILY_RISK_BUDGET: existing position has no protective stop",
                )
            downside = (pos.current_price - pos.stop_loss) if pos.side == PositionSide.LONG else (pos.stop_loss - pos.current_price)
            open_risk += pos.quantity * (
                max(0.0, downside) + pos.stop_loss * friction
            )
        remaining_risk = max(0.0, self.daily_max_loss_usd + min(0.0, portfolio.daily_pnl) - open_risk)
        if remaining_risk <= 0 or portfolio.equity <= 0:
            return RiskDecision(
                approved=False, symbol=signal.symbol, direction=signal.direction,
                reason="DAILY_RISK_BUDGET: no unreserved daily loss budget remains",
            )

        # 6. Size to the remaining daily budget including execution friction.
        size, risk_amount = calculate_atr_position_size(
            equity=portfolio.equity,
            entry_price=signal.entry_price,
            stop_price=signal.stop_price,
            risk_per_trade=min(self.risk_per_trade, remaining_risk / portfolio.equity),
            max_position_equity_ratio=self.max_position_equity_ratio,
            fee_rate=fee_rate,
            slippage_bps=slippage_bps,
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
