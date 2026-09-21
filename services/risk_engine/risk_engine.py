from datetime import datetime
from typing import Any, Optional, Tuple

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
        max_trades_per_day: int = 10,
        max_position_equity_ratio: float = 0.55,
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
        self.max_open_positions_override: Optional[int] = None

        self.circuit_breaker = CircuitBreaker(
            daily_max_loss_usd=self.daily_max_loss_usd,
            max_drawdown_pct=0.10,
            max_trades_per_day=self.max_trades_per_day,
        )

    @property
    def effective_max_open_positions(self) -> int:
        return min(self.max_open_positions_override or self.max_open_positions, 8)

    def set_max_open_positions_override(self, val: Optional[int]) -> Optional[int]:
        if val is None:
            self.max_open_positions_override = None
        else:
            self.max_open_positions_override = min(int(val), 8)
        return self.max_open_positions_override

    def is_daily_profit_locked(self, portfolio: Any) -> Tuple[bool, str, float]:
        """
        Evaluates whether new trade entries are halted due to daily profit lock.
        Evaluates net portfolio performance (Realized PnL + Unrealized floating PnL).
        Returns (is_locked, reason, daily_profit).
        """
        daily_profit = getattr(portfolio, "daily_pnl", None)
        if daily_profit is None:
            realized = getattr(portfolio, "daily_realized_pnl", 0.0)
            unrealized = getattr(portfolio, "unrealized_pnl", 0.0)
            daily_profit = realized + unrealized

        if self.target_mode == "HARD" and daily_profit >= self.daily_target_max:
            return True, f"Hard daily profit target reached (+${daily_profit:.2f} >= ${self.daily_target_max:.2f})", daily_profit
        if self.target_mode == "SOFT" and daily_profit >= self.daily_target_max:
            return True, f"Daily profit ceiling reached under SOFT mode (+${daily_profit:.2f} >= ${self.daily_target_max:.2f})", daily_profit

        return False, "", daily_profit

    def _evaluate_signal_internal(
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
            age_sec = (system_now - lmt).total_seconds()
            if age_sec > 86400.0:
                now = lmt
            else:
                now = system_now
                # Circuit breaker / stale market data invariant: if latest tick is > 120s old in live evaluation
                if age_sec > 120.0:
                    return RiskDecision(
                        approved=False,
                        symbol=signal.symbol,
                        direction=signal.direction,
                        reason=f"Circuit Breaker: Market data stale ({age_sec:.0f}s old > 120s limit)",
                    )
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
            # A candle open timestamp can naturally be up to 1-2 bar periods old at confirmation.
            # Allow 2.5x timeframe TTL (or at least 900s) to avoid falsely rejecting valid live signals.
            max_allowed_age = max(ttl_seconds * 2.5, 900.0)
            if sig_age > max_allowed_age:
                return RiskDecision(
                    approved=False,
                    symbol=signal.symbol,
                    direction=signal.direction,
                    reason=f"Signal expired ({sig_age:.0f}s old > {max_allowed_age:.0f}s TTL for {tf})",
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

        # 0.3 DPO Signal Preference Gate (Faz 3)
        try:
            from services.learning.dpo_signal_gate import dpo_signal_gate
            current_context = getattr(portfolio, "current_context", None) or "UNKNOWN"
            dpo_decision = dpo_signal_gate.evaluate_signal(signal, context=current_context)
            if not dpo_decision.approved:
                logger.info(f"DPO Gate Rejected: {signal.symbol} signal. {dpo_decision.reason}")
                return RiskDecision(
                    approved=False,
                    symbol=signal.symbol,
                    direction=signal.direction,
                    reason=dpo_decision.reason,
                )
        except Exception as e:
            logger.warning(f"DPO gate evaluation check skipped: {e}")

        # 0.4 Live Trading Strategy Validation Gate (Tavizsiz Kural: Unvalidated strategies cannot trade live)
        if getattr(settings, "LIVE_TRADING", False):
            from services.strategy_engine.registry import STRATEGY_REGISTRY, StrategyValidationStatus
            strat_name = getattr(signal, "strategy", "") or (signal.metadata.get("strategy") if signal.metadata else "")
            meta = STRATEGY_REGISTRY.get(strat_name)
            strat_status = getattr(meta, "validation_status", StrategyValidationStatus.RESEARCH_ONLY) if meta else StrategyValidationStatus.RESEARCH_ONLY
            if strat_status != StrategyValidationStatus.VALIDATED:
                logger.critical(f"LIVE TRADING BLOCKED: Strategy '{strat_name}' has status '{strat_status.value}' (not VALIDATED).")
                return RiskDecision(
                    approved=False,
                    symbol=signal.symbol,
                    direction=signal.direction,
                    reason=f"LIVE_TRADING_BLOCKED: Strategy '{strat_name}' is not VALIDATED (requires N>=30, OOS PF>1.30, positive expectancy).",
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
        is_locked, lock_reason, daily_profit = self.is_daily_profit_locked(portfolio)
        if is_locked:
            return RiskDecision(
                approved=False,
                symbol=signal.symbol,
                direction=signal.direction,
                reason=f"{lock_reason}. New entries halted.",
            )
        elif self.target_mode == "SOFT" and daily_profit >= self.daily_target_min:
            # Under SOFT mode, if minimum target reached, only take exceptional signals (Score >= 75)
            meta = signal.metadata or {}
            signal_score = (
                meta.get("signal_score")
                or meta.get("score")
                or getattr(signal, "opportunity_score", None)
                or 60.0
            )
            if signal_score < 75.0:
                return RiskDecision(
                    approved=False,
                    symbol=signal.symbol,
                    direction=signal.direction,
                    reason=f"Soft daily target active (+${daily_profit:.2f}); rejected signal with score {signal_score} < 75 (High quality filter).",
                )

        # 3. Open Positions Limit (Hard ceiling invariant: <= 8)
        eff_max_open = min(self.max_open_positions_override or self.max_open_positions, 8)
        if len(portfolio.open_positions) >= eff_max_open:
            return RiskDecision(
                approved=False,
                symbol=signal.symbol,
                direction=signal.direction,
                reason=f"Max open positions reached ({len(portfolio.open_positions)}/{eff_max_open})",
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
        # Apply 15% haircut buffer on remaining unreserved risk to guarantee that
        # worst-case adverse slippage below the stop and round-trip taker commissions
        # can never breach the daily loss limit (Audit Remediation Phase 1).
        unbuffered_remaining_risk = max(0.0, self.daily_max_loss_usd + min(0.0, portfolio.daily_pnl) - open_risk)
        remaining_risk = unbuffered_remaining_risk * 0.85
        is_cb_suspended = getattr(self.circuit_breaker, "is_suspended", False)
        if (remaining_risk <= 0 or portfolio.equity <= 0) and not is_cb_suspended:
            return RiskDecision(
                approved=False, symbol=signal.symbol, direction=signal.direction,
                reason="DAILY_RISK_LOCK: DAILY_RISK_BUDGET: no unreserved daily loss budget remains (15% haircut buffer active)",
            )

        # 6. Size to the remaining daily budget including execution friction.
        effective_budget_ratio = (remaining_risk / portfolio.equity) if (remaining_risk > 0 and not is_cb_suspended) else self.risk_per_trade
        effective_max_equity_ratio = min(self.max_position_equity_ratio, 0.15) if self.risk_per_trade <= 0.01 else self.max_position_equity_ratio
        size, risk_amount = calculate_atr_position_size(
            equity=portfolio.equity,
            entry_price=signal.entry_price,
            stop_price=signal.stop_price,
            risk_per_trade=min(self.risk_per_trade, effective_budget_ratio),
            max_position_equity_ratio=effective_max_equity_ratio,
            fee_rate=fee_rate,
            slippage_bps=slippage_bps,
        )

        # 6.05 Cash Balance Guard: Cap order notional so it does not exceed available cash balance
        avail = getattr(portfolio, "available_balance", None)
        available_cash = max(0.0, avail if avail is not None else getattr(portfolio, "balance", portfolio.equity))
        max_affordable_notional = available_cash * 0.98  # Keep 2% buffer for taker fee + slippage
        if (size * signal.entry_price) > max_affordable_notional:
            size = max(0.0, max_affordable_notional / signal.entry_price)

        if size <= 0:
            return RiskDecision(
                approved=False,
                symbol=signal.symbol,
                direction=signal.direction,
                reason="Calculated position size is zero or insufficient available cash balance",
            )

        # 6.1 Binance Minimum Notional (MIN_NOTIONAL) Guard
        notional_value = size * signal.entry_price
        min_notional = getattr(settings, "MIN_NOTIONAL_USDT", 11.0)
        if notional_value < min_notional:
            return RiskDecision(
                approved=False,
                symbol=signal.symbol,
                direction=signal.direction,
                reason=f"REJECTED_BELOW_MIN_NOTIONAL: Order notional value ${notional_value:.2f} is below minimum allowable ${min_notional:.2f} USDT",
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

    def evaluate_signal(
        self,
        signal: Signal,
        portfolio: PortfolioState,
        latest_market_time: Optional[datetime] = None,
        current_time: Optional[datetime] = None,
        closed_positions: Optional[list] = None,
    ) -> RiskDecision:
        from datetime import timezone

        decision = self._evaluate_signal_internal(
            signal=signal,
            portfolio=portfolio,
            latest_market_time=latest_market_time,
            current_time=current_time,
            closed_positions=closed_positions,
        )

        # Step 1: Record EVERY evaluated signal (approved or rejected) into signals table
        try:
            from database.repositories.signal_repo import SignalRepository
            from apps.api.app.api.state import RUNTIME_STATE

            sig_id = getattr(signal, "signal_id", None)
            active_lvl = getattr(self, "active_profile_level", None) or RUNTIME_STATE.get("active_profile_level", 1)
            meta = signal.metadata or {}
            sig_score = meta.get("signal_score") or meta.get("score")
            opp_score = getattr(signal, "opportunity_score", None) or meta.get("opportunity_score")
            reg_name = signal.regime.value if hasattr(signal.regime, "value") else str(signal.regime)
            sig_ts = signal.timestamp or datetime.now(timezone.utc)

            db_id = SignalRepository.record_signal_sync(
                symbol=signal.symbol,
                strategy_id=signal.strategy,
                direction=signal.direction.value if hasattr(signal.direction, "value") else str(signal.direction),
                entry_price=signal.entry_price,
                stop_price=signal.stop_price,
                take_profit=signal.take_profit,
                confidence=signal.confidence,
                regime=reg_name,
                signal_id=sig_id,
                risk_profile_level=active_lvl,
                signal_score=sig_score,
                opportunity_score=opp_score,
                approved=decision.approved,
                rejection_reason=decision.reason if not decision.approved else None,
                regime_state=reg_name,
                timestamp=sig_ts,
            )
            if db_id and not getattr(decision, "signal_id", None):
                decision.signal_id = sig_id or f"SIG_{db_id}"
        except Exception as e:
            logger.debug(f"Signal recording to signals table skipped: {e}")

        # Step 2: Record rejected signals into risk_events table (HATA 3)
        if not decision.approved:
            try:
                from database.repositories.risk_repo import RiskRepository

                reason = decision.reason or "REJECTED_UNKNOWN"
                event_type = "RISK_SIGNAL_REJECTED"
                if "DAILY_RISK_LOCK" in reason or "Daily loss" in reason:
                    event_type = "DAILY_RISK_LOCK"
                elif "DPO" in reason:
                    event_type = "DPO_GATE_REJECT"
                elif "OPPORTUNITY_SCORE" in reason:
                    event_type = "LOW_OPPORTUNITY_SCORE"
                elif "Mathematical Expectancy" in reason:
                    event_type = "EXPECTANCY_HALT"
                elif "Signal expired" in reason:
                    event_type = "SIGNAL_EXPIRED"
                elif "Spot Mode" in reason:
                    event_type = "SPOT_MODE_SHORT_BLOCKED"
                elif "Max open positions" in reason:
                    event_type = "MAX_OPEN_POSITIONS_REACHED"
                elif "Existing position" in reason:
                    event_type = "DUPLICATE_POSITION"
                elif "MIN_NOTIONAL" in reason:
                    event_type = "MIN_NOTIONAL_BREACH"
                elif "insufficient reward" in reason:
                    event_type = "NET_RISK_REWARD_INSUFFICIENT"
                elif "daily loss budget" in reason:
                    event_type = "DAILY_RISK_BUDGET_EXHAUSTED"

                RiskRepository.record_event_sync(
                    event_type=event_type,
                    description=reason,
                    symbol=signal.symbol,
                    metadata={
                        "strategy": signal.strategy,
                        "direction": signal.direction.value if hasattr(signal.direction, "value") else str(signal.direction),
                        "confidence": getattr(signal, "confidence", 0.0),
                        "entry_price": getattr(signal, "entry_price", 0.0),
                        "daily_pnl": getattr(portfolio, "daily_pnl", 0.0),
                        "equity": getattr(portfolio, "equity", 0.0),
                    },
                )
            except Exception as e:
                logger.warning(f"Failed to record risk rejection event in risk_events: {e}")

        return decision

    def record_executed_trade(self):
        """Called ONLY upon actual order execution and fill (AUDIT-05)."""
        self.circuit_breaker.record_trade()
        logger.info(
            f"Trade recorded in circuit breaker ({self.circuit_breaker.trades_today_count}/{self.circuit_breaker.max_trades_per_day} trades today)."
        )
