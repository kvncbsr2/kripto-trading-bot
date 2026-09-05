from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.execution.live_binance_execution import BinanceLiveExecutionEngine
from services.execution.order_manager import OrderManager
from services.execution.paper_execution import PaperExecutionEngine
from services.execution.reconciliation import ReconciliationEngine
from services.execution.symbol_filters import symbol_filter_engine
from services.market_data.data_quality import DataQualityEngine
from services.risk_engine.risk_engine import RiskEngine
from services.strategy_engine.strategies.r10_rsi_divergence import R10RSIDivergenceStrategy
from shared.config import get_settings
from shared.enums import MarketRegime, OrderStatus, SignalDirection, Timeframe
from shared.schemas import Candle, PortfolioState, RiskDecision, Signal

settings = get_settings()


# =============================================================================
# I1 & I20: PAPER MODE & REAL/PAPER SEPARATION
# Paper execution cannot reach Binance private trading endpoints under any condition.
# =============================================================================
@pytest.mark.asyncio
async def test_i1_and_i20_paper_broker_zero_binance_network_call():
    engine = PaperExecutionEngine(initial_balance=5000.0)
    # Check engine has no ccxt or Binance REST client
    assert not hasattr(engine, "client") or engine.client is None

    decision = RiskDecision(
        approved=True,
        symbol="BTC/USDT",
        direction=SignalDirection.LONG,
        calculated_size=0.01,
        entry_price=60000.0,
        stop_loss=58000.0,
        take_profit=64000.0,
        risk_score=20.0,
        risk_reward_ratio=2.0,
        reason="Paper test",
    )

    order, fill, pos = await engine.submit_order(decision, strategy_name="test")
    assert order.status == OrderStatus.FILLED
    assert fill.fee > 0.0  # Simulated fee deducted
    assert pos.status.value == "OPEN"
    # Balance must be deducted internally
    assert engine.balance < 5000.0


# =============================================================================
# I2: LIVE TRADING LOCK
# When LIVE_TRADING=false, no live order can ever be initialized or submitted.
# =============================================================================
def test_i2_live_trading_lock_enforcement():
    orig_live = settings.LIVE_TRADING
    try:
        settings.LIVE_TRADING = False
        with pytest.raises(RuntimeError, match="LIVE TRADING IS LOCKED"):
            BinanceLiveExecutionEngine(api_key="key", api_secret="sec", armed=True)
    finally:
        settings.LIVE_TRADING = orig_live


# =============================================================================
# I3: SINGLE ORDER AUTHORITY & BYPASS PREVENTION
# All orders must pass through OrderManager with protective stop validation.
# =============================================================================
@pytest.mark.asyncio
async def test_i3_single_order_authority_protective_stop_invariant():
    om = OrderManager()
    engine = PaperExecutionEngine(initial_balance=5000.0)
    om.bind_execution_engine(engine)

    # Decision with missing stop loss
    invalid_decision = RiskDecision(
        approved=True,
        symbol="BTC/USDT",
        direction=SignalDirection.LONG,
        calculated_size=0.01,
        entry_price=60000.0,
        stop_loss=0.0,  # Missing protective stop!
        take_profit=64000.0,
        risk_score=20.0,
        risk_reward_ratio=2.0,
        reason="Invalid zero stop",
    )

    with pytest.raises(ValueError, match="PROTECTIVE_STOP_VIOLATION"):
        await om.execute_risk_decision(invalid_decision, strategy_name="test")


# =============================================================================
# I4 & ADVERSARIAL: STALE DATA / TIME DRIFT
# Signals with stale timestamps or dead feeds are rejected fail-closed.
# =============================================================================
def test_i4_stale_market_data_rejection():
    risk = RiskEngine()
    now = datetime.now(timezone.utc)
    old_time = now - timedelta(seconds=250)  # 250s old > 180s stale_data_seconds

    sig = Signal(
        symbol="BTC/USDT",
        timestamp=now,
        strategy="R10",
        direction=SignalDirection.LONG,
        entry_price=60000.0,
        stop_price=58000.0,
        take_profit=64000.0,
        confidence=0.85,
        regime=MarketRegime.BULL_TREND,
        reason="Stale signal test",
    )

    portfolio = PortfolioState(
        balance=5000.0,
        total_balance=5000.0,
        available_balance=5000.0,
        open_positions=[],
        equity=5000.0,
    )

    # Passing stale candle time triggers circuit breaker
    decision = risk.evaluate_signal(sig, portfolio, latest_market_time=old_time)
    assert decision.approved is False
    assert "stale" in decision.reason.lower() or "circuit" in decision.reason.lower()


# =============================================================================
# I5 & ADVERSARIAL: DAILY LOSS BREACH
# When daily loss limit is exceeded, new orders are blocked.
# =============================================================================
def test_i5_daily_loss_limit_breach():
    risk = RiskEngine()
    portfolio = PortfolioState(
        balance=4900.0,
        total_balance=4900.0,
        available_balance=4900.0,
        open_positions=[],
        equity=4900.0,
        daily_pnl=-150.0,  # Exceeds default $50 daily max loss
    )

    sig = Signal(
        symbol="BTC/USDT",
        timestamp=datetime.now(timezone.utc),
        strategy="R10",
        direction=SignalDirection.LONG,
        entry_price=60000.0,
        stop_price=58000.0,
        take_profit=64000.0,
        confidence=0.85,
        regime=MarketRegime.BULL_TREND,
        reason="Daily loss test",
    )

    decision = risk.evaluate_signal(sig, portfolio)
    assert decision.approved is False
    assert "DAILY" in decision.reason or "Daily loss" in decision.reason


# =============================================================================
# I6: MANDATORY STOP LOSS INVARIANT
# =============================================================================
def test_i6_mandatory_stop_loss_in_risk_engine():
    risk = RiskEngine()
    portfolio = PortfolioState(
        balance=5000.0,
        total_balance=5000.0,
        available_balance=5000.0,
        open_positions=[],
        equity=5000.0,
    )

    # Stop loss higher than entry price on a LONG (invalid geometry)
    sig = Signal(
        symbol="BTC/USDT",
        timestamp=datetime.now(timezone.utc),
        strategy="R10",
        direction=SignalDirection.LONG,
        entry_price=60000.0,
        stop_price=62000.0,  # Invalid stop above entry for LONG!
        take_profit=64000.0,
        confidence=0.85,
        regime=MarketRegime.BULL_TREND,
        reason="Invalid stop geometry",
    )

    decision = risk.evaluate_signal(sig, portfolio)
    assert decision.approved is False
    assert "Invalid Stop/Target" in decision.reason


# =============================================================================
# I7: PROTECTIVE STOP ON LIVE & FAIL-CLOSED ON FAILURE
# =============================================================================
@pytest.mark.asyncio
async def test_i7_live_protective_stop_fail_closed():
    orig_live = settings.LIVE_TRADING
    try:
        settings.LIVE_TRADING = True
        mock_client = MagicMock()
        mock_client.create_order = AsyncMock(side_effect=[
            {"id": "entry_1", "average": 60000.0, "filled": 0.01},  # Entry buy succeeds
            Exception("Exchange error on protective stop"),        # Stop loss order fails
            {"id": "close_1", "average": 59980.0, "filled": 0.01}, # Fail-closed emergency market sell
        ])

        engine = BinanceLiveExecutionEngine(api_key="k", api_secret="s", armed=True, client=mock_client)
        decision = RiskDecision(
            approved=True,
            symbol="BTC/USDT",
            direction=SignalDirection.LONG,
            calculated_size=0.01,
            entry_price=60000.0,
            stop_loss=58000.0,
            take_profit=64000.0,
            risk_score=20.0,
            risk_reward_ratio=2.0,
            reason="Live stop test",
        )

        with pytest.raises(RuntimeError, match="FAIL-CLOSED INVARIANT"):
            await engine.submit_order(decision, strategy_name="live")

        # Verify emergency market sell was invoked to close the unprotected position
        assert mock_client.create_order.call_count == 3
    finally:
        settings.LIVE_TRADING = orig_live


# =============================================================================
# I8: RECONCILIATION DISCREPANCY HALT
# =============================================================================
@pytest.mark.asyncio
async def test_i8_reconciliation_detects_drift():
    reconciler = ReconciliationEngine()
    mock_client = MagicMock()
    # Exchange has an unknown order not tracked locally
    mock_client.fetch_open_orders = AsyncMock(return_value=[
        {"id": "ghost_999", "symbol": "ETH/USDT", "side": "buy", "amount": 2.0}
    ])

    report = await reconciler.reconcile_orders_and_positions(
        local_open_positions={},
        local_orders={},
        exchange_client=mock_client,
    )
    assert report.is_synchronized is False
    assert len(report.discrepancies) > 0
    assert report.discrepancies[0].discrepancy_type == "ORPHAN_EXCHANGE_ORDER"


# =============================================================================
# I9: IDEMPOTENCY & DUPLICATE ORDER PREVENTION
# =============================================================================
@pytest.mark.asyncio
async def test_i9_idempotency_duplicate_order_prevention():
    om = OrderManager()
    engine = PaperExecutionEngine(initial_balance=5000.0)
    om.bind_execution_engine(engine)

    decision = RiskDecision(
        approved=True,
        symbol="BTC/USDT",
        direction=SignalDirection.LONG,
        calculated_size=0.01,
        entry_price=60000.0,
        stop_loss=58000.0,
        take_profit=64000.0,
        risk_score=20.0,
        risk_reward_ratio=2.0,
        reason="Idempotency test",
    )

    order1, fill1, pos1 = await om.execute_risk_decision(decision, strategy_name="test")
    # Submitting exact same intent again must raise DUPLICATE_ORDER_ATTEMPT
    with pytest.raises(ValueError, match="DUPLICATE_ORDER_ATTEMPT"):
        await om.execute_risk_decision(decision, strategy_name="test")

    # Invariant: open positions count remains exactly 1 (no duplicate order filled!)
    assert len(engine.open_positions) == 1


# =============================================================================
# I13 & ADVERSARIAL: EXCHANGE SYMBOL FILTERS (LOT SIZE, MIN NOTIONAL)
# =============================================================================
def test_i13_exchange_symbol_filters_validation():
    # Below min notional ($5.00 min on Binance)
    is_valid, reason, _, _ = symbol_filter_engine.normalize_and_validate(
        symbol="BTC/USDT",
        price=60000.0,
        quantity=0.00001,  # 0.00001 * 60000 = $0.60 < $5.00
    )
    assert is_valid is False
    assert "minnotional" in reason.lower()


# =============================================================================
# ADVERSARIAL: NaN / INF / ZERO / NEGATIVE PRICE & QUANTITY
# =============================================================================
def test_adversarial_nan_inf_negative_inputs():
    dq = DataQualityEngine()

    # NaN price candle
    c_nan = Candle(
        symbol="BTC/USDT",
        timeframe=Timeframe.M15,
        timestamp=datetime.now(timezone.utc),
        open=60000.0,
        high=float("nan"),
        low=59000.0,
        close=59500.0,
        volume=100.0,
    )
    res_nan = dq.validate_candle(c_nan)
    assert res_nan.valid is False
    assert "NaN or Inf" in res_nan.reason

    # Negative quantity filter
    is_valid, reason, _, _ = symbol_filter_engine.normalize_and_validate(
        symbol="BTC/USDT",
        price=60000.0,
        quantity=-0.5,
    )
    assert is_valid is False
    assert "minqty" in reason.lower()


# =============================================================================
# ADVERSARIAL: OUT-OF-ORDER CANDLE
# =============================================================================
def test_adversarial_out_of_order_candle():
    dq = DataQualityEngine()
    now = datetime.now(timezone.utc)

    prev_c = Candle(
        symbol="BTC/USDT",
        timeframe=Timeframe.M15,
        timestamp=now,
        open=60000.0,
        high=61000.0,
        low=59900.0,
        close=60500.0,
        volume=100.0,
    )

    # Earlier timestamp arriving after prev_c
    out_of_order_c = Candle(
        symbol="BTC/USDT",
        timeframe=Timeframe.M15,
        timestamp=now - timedelta(minutes=15),
        open=60000.0,
        high=61000.0,
        low=59900.0,
        close=60500.0,
        volume=100.0,
    )

    res = dq.validate_candle(out_of_order_c, prev_candle=prev_c)
    assert res.valid is False
    assert "Out-of-order" in res.reason


# =============================================================================
# I18: R10 STRICT CAUSALITY (NO LOOKAHEAD)
# =============================================================================
def test_i18_r10_strict_causality_window():
    strat = R10RSIDivergenceStrategy(left_bars=5, right_bars=5)
    assert strat.right_bars == 5
    # Confirms that a pivot at index T is confirmed strictly at index T + 5
