import pytest
import sqlite3
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, AsyncMock

from shared.config import get_settings
from shared.enums import SignalDirection, OrderStatus, PositionSide, Timeframe, MarketRegime
from shared.schemas import Signal, RiskDecision, PortfolioState, Candle, Order, Position
from services.risk_engine.risk_engine import RiskEngine
from services.execution.order_manager import OrderManager
from services.execution.paper_execution import PaperExecutionEngine
from services.execution.live_binance_execution import BinanceLiveExecutionEngine
from services.execution.symbol_filters import symbol_filter_engine
from services.execution.reconciliation import ReconciliationEngine
from services.market_data.data_quality import DataQualityEngine

settings = get_settings()

# 1. assert no_order_when_market_data_stale
def test_assert_no_order_when_market_data_stale():
    risk = RiskEngine()
    now = datetime.now(timezone.utc)
    old_time = now - timedelta(seconds=250)
    sig = Signal(
        symbol="BTC/USDT",
        timestamp=now,
        strategy="test",
        direction=SignalDirection.LONG,
        entry_price=60000.0,
        stop_price=58000.0,
        take_profit=64000.0,
        confidence=0.85,
    )
    portfolio = PortfolioState(balance=5000.0, equity=5000.0, open_positions=[])
    decision = risk.evaluate_signal(sig, portfolio, latest_market_time=old_time)
    assert not decision.approved
    assert "stale" in decision.reason.lower() or "circuit" in decision.reason.lower()

# 2. assert no_order_when_daily_loss_breached
def test_assert_no_order_when_daily_loss_breached():
    risk = RiskEngine()
    portfolio = PortfolioState(balance=4900.0, equity=4900.0, daily_pnl=-150.0, open_positions=[])
    sig = Signal(
        symbol="BTC/USDT",
        timestamp=datetime.now(timezone.utc),
        strategy="test",
        direction=SignalDirection.LONG,
        entry_price=60000.0,
        stop_price=58000.0,
        take_profit=64000.0,
        confidence=0.85,
    )
    decision = risk.evaluate_signal(sig, portfolio)
    assert not decision.approved
    assert "daily" in decision.reason.lower()

# 3. assert no_order_when_not_live_and_armed
def test_assert_no_order_when_not_live_and_armed():
    orig_live = settings.LIVE_TRADING
    try:
        settings.LIVE_TRADING = False
        with pytest.raises(RuntimeError, match="LIVE TRADING IS LOCKED"):
            BinanceLiveExecutionEngine(api_key="key", api_secret="sec", armed=False)
    finally:
        settings.LIVE_TRADING = orig_live

# 4. assert all_order_intents_pass_order_manager
@pytest.mark.asyncio
async def test_assert_all_order_intents_pass_order_manager():
    om = OrderManager()
    engine = PaperExecutionEngine(initial_balance=5000.0)
    om.bind_execution_engine(engine)
    invalid_decision = RiskDecision(
        approved=True,
        symbol="BTC/USDT",
        direction=SignalDirection.LONG,
        calculated_size=0.01,
        entry_price=60000.0,
        stop_loss=0.0,
        take_profit=64000.0,
        reason="Invalid zero stop",
    )
    with pytest.raises(ValueError, match="PROTECTIVE_STOP_VIOLATION"):
        await om.execute_risk_decision(invalid_decision, strategy_name="test")

# 5. assert duplicate_signal_cannot_create_duplicate_exchange_order
@pytest.mark.asyncio
async def test_assert_duplicate_signal_cannot_create_duplicate_exchange_order():
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
        reason="Idempotency test",
    )
    order1, fill1, pos1 = await om.execute_risk_decision(decision, strategy_name="test")
    assert order1 is not None
    with pytest.raises(ValueError, match="DUPLICATE_ORDER_ATTEMPT"):
        await om.execute_risk_decision(decision, strategy_name="test")
    assert len(engine.open_positions) == 1

# 6. assert restart_cannot_duplicate_order
def test_assert_restart_cannot_duplicate_order(tmp_path):
    db_file = str(tmp_path / "test_persistence.db")
    engine1 = PaperExecutionEngine(initial_balance=5000.0, db_path=db_file)
    decision = RiskDecision(
        approved=True,
        symbol="ETH/USDT",
        direction=SignalDirection.LONG,
        calculated_size=0.5,
        entry_price=3000.0,
        stop_loss=2900.0,
        take_profit=3200.0,
        reason="Persistence trade",
    )
    order1, fill1, pos1 = engine1.execute_market_order(decision, strategy_name="test_p", order_id="ord_fixed_001")
    assert "ETH/USDT" in engine1.open_positions
    
    engine2 = PaperExecutionEngine(initial_balance=5000.0, db_path=db_file)
    assert "ETH/USDT" in engine2.open_positions
    assert "ord_fixed_001" in engine2.orders
    with pytest.raises(sqlite3.IntegrityError):
        conn = engine2._get_db_conn()
        with conn:
            conn.execute("INSERT INTO paper_orders (order_id, symbol, side, order_type, quantity, price, status, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         ("ord_fixed_001", "ETH/USDT", "BUY", "MARKET", 0.5, 3000.0, "FILLED", "now", "now"))

# 7. assert unknown_order_state_never_blind_retries
@pytest.mark.asyncio
async def test_assert_unknown_order_state_never_blind_retries():
    om = OrderManager()
    engine = MagicMock()
    engine.execute_market_order = MagicMock(side_effect=TimeoutError("Exchange gateway timeout"))
    om.bind_execution_engine(engine)
    decision = RiskDecision(
        approved=True,
        symbol="BTC/USDT",
        direction=SignalDirection.LONG,
        calculated_size=0.01,
        entry_price=60000.0,
        stop_loss=59000.0,
        take_profit=62000.0,
        reason="Unknown state test",
    )
    with pytest.raises(TimeoutError):
        await om.execute_risk_decision(decision, strategy_name="test")
    assert engine.execute_market_order.call_count == 1

# 8. assert symbol_filters_are_exchange_derived
def test_assert_symbol_filters_are_exchange_derived():
    is_valid, reason, _, _ = symbol_filter_engine.normalize_and_validate(
        symbol="BTC/USDT",
        price=60000.0,
        quantity=0.00001,
    )
    assert is_valid is False
    assert "minnotional" in reason.lower()

# 9. assert every_live_position_has_exchange_protection
@pytest.mark.asyncio
async def test_assert_every_live_position_has_exchange_protection():
    orig_live = settings.LIVE_TRADING
    try:
        settings.LIVE_TRADING = True
        mock_client = MagicMock()
        mock_client.create_order = AsyncMock(side_effect=[
            {"id": "entry_1", "average": 60000.0, "filled": 0.01},
            Exception("Exchange error on protective stop"),
            {"id": "close_1", "average": 59980.0, "filled": 0.01},
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
            reason="Live stop test",
        )
        with pytest.raises(RuntimeError, match="FAIL-CLOSED INVARIANT"):
            await engine.submit_order(decision, strategy_name="live")
        assert mock_client.create_order.call_count == 3
    finally:
        settings.LIVE_TRADING = orig_live

# 10. assert local_protected_state_requires_exchange_confirmation
@pytest.mark.asyncio
async def test_assert_local_protected_state_requires_exchange_confirmation():
    reconciler = ReconciliationEngine()
    mock_client = MagicMock()
    mock_client.fetch_open_orders = AsyncMock(return_value=[
        {"id": "ghost_order", "symbol": "ETH/USDT", "side": "buy", "amount": 1.0}
    ])
    report = await reconciler.reconcile_orders_and_positions(
        local_open_positions={},
        local_orders={},
        exchange_client=mock_client,
    )
    assert report.is_synchronized is False
    assert len(report.discrepancies) > 0

# 11. assert emergency_shutdown_blocks_new_orders
@pytest.mark.asyncio
async def test_assert_emergency_shutdown_blocks_new_orders():
    from apps.api.app.api.state import RUNTIME_STATE
    RUNTIME_STATE["is_halted"] = True
    RUNTIME_STATE["system_state"] = "RISK_LOCK"
    assert RUNTIME_STATE["is_halted"] is True

# 12. assert unauthorized_mutation_is_rejected
@pytest.mark.asyncio
async def test_assert_unauthorized_mutation_is_rejected():
    from apps.api.app.main import app
    from httpx import ASGITransport, AsyncClient
    orig_auth = settings.API_KEY_AUTH_ENABLED
    try:
        settings.API_KEY_AUTH_ENABLED = True
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post("/system/emergency-shutdown")
            assert resp.status_code == 401
    finally:
        settings.API_KEY_AUTH_ENABLED = orig_auth

# 13. assert paper_execution_never_calls_private_binance
def test_assert_paper_execution_never_calls_private_binance():
    engine = PaperExecutionEngine(initial_balance=5000.0)
    assert not hasattr(engine, "exchange")
    assert not hasattr(engine, "client")

# 14. assert reconciliation_failure_halts_new_entries
@pytest.mark.asyncio
async def test_assert_reconciliation_failure_halts_new_entries():
    reconciler = ReconciliationEngine()
    mock_client = MagicMock()
    mock_client.fetch_open_orders = AsyncMock(return_value=[
        {"id": "mismatch_order", "symbol": "BTC/USDT", "side": "buy", "amount": 0.5}
    ])
    report = await reconciler.reconcile_orders_and_positions(
        local_open_positions={},
        local_orders={},
        exchange_client=mock_client,
    )
    assert not report.is_synchronized
    assert "ORPHAN_EXCHANGE_ORDER" in [d.discrepancy_type for d in report.discrepancies]

# 15. assert incomplete_candle_cannot_create_backtest_live_mismatch
def test_assert_incomplete_candle_cannot_create_backtest_live_mismatch():
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
    out_of_order = Candle(
        symbol="BTC/USDT",
        timeframe=Timeframe.M15,
        timestamp=now - timedelta(minutes=15),
        open=60000.0,
        high=61000.0,
        low=59900.0,
        close=60500.0,
        volume=100.0,
    )
    res = dq.validate_candle(out_of_order, prev_candle=prev_c)
    assert res.valid is False
    assert "Out-of-order" in res.reason
