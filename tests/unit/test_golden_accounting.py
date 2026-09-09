import pytest
from unittest.mock import AsyncMock, MagicMock
from services.execution.paper_execution import PaperExecutionEngine
from services.execution.reconciliation import ReconciliationEngine
from services.execution.live_binance_execution import BinanceLiveExecutionEngine
from shared.enums import SignalDirection, PositionStatus, OrderStatus
from shared.schemas import RiskDecision, Position


def test_golden_accounting_closed_trade():
    """
    Deterministic Golden Accounting Test:
    Initial: $5,000.00
    Entry: Buy 1 unit at $1,000.00.
           Fee rate = 0.1% (0.001).
           Entry notional = $1,000.00, entry fee = $1.00.
    Exit:  Sell 1 unit at $1,050.00.
           Exit notional = $1,050.00, exit fee = $1.05.
    Expected:
           Gross PnL = +$50.00
           Total trade fees = $2.05 ($1.00 entry + $1.05 exit)
           True Net PnL = $50.00 - $2.05 = +$47.95
           Final Cash Available = $5,047.95
           Final Reserved Balance = $0.00
           Final Total Balance = $5,047.95
           Final Equity = $5,047.95
           Total Realized PnL = +$47.95
           Final Equity - Initial Capital = +$47.95
    """
    engine = PaperExecutionEngine(
        initial_balance=5000.0,
        taker_fee=0.001,
        maker_fee=0.0005,
        slippage_bps=0.0,  # 0 slippage for exact accounting verification
        db_path=":memory:",  # isolated memory SQLite
    )

    # 1. Verification at initial state
    assert engine.balance == 5000.0
    assert engine.available_balance == 5000.0
    assert engine.reserved_balance == 0.0
    assert engine.equity == 5000.0
    assert engine.total_realized_pnl == 0.0
    assert engine.total_unrealized_pnl == 0.0

    # 2. Execute Entry Market Order
    decision = RiskDecision(
        approved=True,
        symbol="BTC/USDT",
        direction=SignalDirection.LONG,
        calculated_size=1.0,
        entry_price=1000.0,
        stop_loss=950.0,
        take_profit=1100.0,
        risk_amount=50.0,
    )
    order, fill, pos = engine.execute_market_order(decision, strategy_name="golden_test")

    # Invariants immediately after entry
    assert fill.price == 1000.0
    assert fill.fee == 1.00
    assert pos.fees_paid == 1.00
    assert pos.quantity == 1.0
    assert engine.reserved_balance == 1000.0
    assert engine.available_balance == 3999.0  # 5000 - 1000 - 1
    assert engine.balance == 4999.0            # 5000 - 1
    assert engine.equity == 4999.0             # 3999 cash + 1000 market value
    assert engine.total_unrealized_pnl == 0.0

    # 3. Close position at $1050.00
    closed_pos = engine.close_position(symbol="BTC/USDT", exit_price=1050.0, reason="TAKE_PROFIT")

    assert closed_pos is not None
    assert closed_pos.status == PositionStatus.CLOSED
    # True net PnL = 50.00 gross - (1.00 entry fee + 1.05 exit fee) = 47.95
    assert closed_pos.realized_pnl == 47.95
    assert closed_pos.fees_paid == 2.05

    # 4. Final Account Accounting Assertions
    assert engine.reserved_balance == 0.0
    assert engine.available_balance == 5047.95
    assert engine.balance == 5047.95
    assert engine.equity == 5047.95
    assert engine.total_realized_pnl == 47.95
    assert round(engine.equity - engine.initial_balance, 2) == round(engine.total_realized_pnl, 2)
    assert engine.total_fees_paid == 2.05


def test_golden_accounting_open_position_valuation():
    """
    Verifies that Equity = Available Cash + Current Market Value of Held Assets
    Cash = 3999 (after 1000 cost + 1 fee paid from 5000 initial)
    Market value goes to 1020 -> Equity must equal 3999 + 1020 = 5019
    """
    engine = PaperExecutionEngine(
        initial_balance=5000.0,
        taker_fee=0.001,
        slippage_bps=0.0,
        db_path=":memory:",
    )

    decision = RiskDecision(
        approved=True,
        symbol="ETH/USDT",
        direction=SignalDirection.LONG,
        calculated_size=1.0,
        entry_price=1000.0,
        stop_loss=900.0,
        take_profit=1200.0,
        risk_amount=100.0,
    )
    engine.execute_market_order(decision)

    # Simulate price moving to 1020.0
    pos = engine.open_positions["ETH/USDT"]
    pos.current_price = 1020.0
    pos.unrealized_pnl = (1020.0 - 1000.0) * 1.0  # +20.0

    # Test exact invariant
    market_value = pos.current_price * pos.quantity  # 1020.0
    expected_equity = engine.available_balance + market_value  # 3999.0 + 1020.0 = 5019.0

    assert engine.available_balance == 3999.0
    assert engine.reserved_balance == 1000.0
    assert engine.balance == 4999.0
    assert engine.total_unrealized_pnl == 20.0
    assert engine.equity == 5019.0
    assert engine.equity == round(expected_equity, 2)


def test_golden_accounting_multiple_positions_lifecycle():
    """
    Tests opening multiple positions, closing one with profit, closing one with loss,
    and verifying exact accounting balance invariants at every step.
    """
    engine = PaperExecutionEngine(
        initial_balance=5000.0,
        taker_fee=0.001,
        slippage_bps=0.0,
        db_path=":memory:",
    )

    # Trade 1: Buy 2 SOL @ $100 -> Cost 200, fee 0.20
    d1 = RiskDecision(
        approved=True, symbol="SOL/USDT", direction=SignalDirection.LONG,
        calculated_size=2.0, entry_price=100.0, stop_loss=90.0, take_profit=120.0, risk_amount=20.0
    )
    engine.execute_market_order(d1)

    # Trade 2: Buy 10 AVAX @ $20 -> Cost 200, fee 0.20
    d2 = RiskDecision(
        approved=True, symbol="AVAX/USDT", direction=SignalDirection.LONG,
        calculated_size=10.0, entry_price=20.0, stop_loss=18.0, take_profit=25.0, risk_amount=20.0
    )
    engine.execute_market_order(d2)

    assert engine.reserved_balance == 400.0
    assert engine.available_balance == 5000.0 - 400.0 - 0.40  # 4599.60
    assert engine.balance == 4999.60
    assert engine.equity == 4999.60

    # Close SOL at $110 (+10% -> Gross PnL = +$20, exit fee = 220 * 0.001 = 0.22)
    # Total fees for SOL = 0.20 + 0.22 = 0.42. Net PnL = 20 - 0.42 = +19.58
    pos_sol = engine.close_position("SOL/USDT", exit_price=110.0, reason="TARGET")
    assert pos_sol.realized_pnl == 19.58
    assert pos_sol.fees_paid == 0.42

    # Account state:
    # SOL returned cash proceeds = 220 - 0.22 = 219.78
    # Available balance = 4599.60 + 219.78 = 4819.38
    # Reserved balance = 200.0 (AVAX still open)
    # Total balance = 4819.38 + 200.0 = 5019.38
    assert engine.reserved_balance == 200.0
    assert round(engine.available_balance, 2) == 4819.38
    assert round(engine.balance, 2) == 5019.38
    assert engine.total_realized_pnl == 19.58

    # Close AVAX at $18 (-10% -> Gross PnL = -$20, exit fee = 180 * 0.001 = 0.18)
    # Total fees for AVAX = 0.20 + 0.18 = 0.38. Net PnL = -20 - 0.38 = -20.38
    pos_avax = engine.close_position("AVAX/USDT", exit_price=18.0, reason="STOP")
    assert pos_avax.realized_pnl == -20.38
    assert pos_avax.fees_paid == 0.38

    # Final verification:
    # Cumulative realized PnL = 19.58 - 20.38 = -0.80
    # Final balance = 5000.00 - 0.80 = 4999.20
    assert engine.reserved_balance == 0.0
    assert round(engine.available_balance, 2) == 4999.20
    assert round(engine.balance, 2) == 4999.20
    assert engine.equity == 4999.20
    assert round(engine.total_realized_pnl, 2) == -0.80
    assert round(engine.equity - engine.initial_balance, 2) == round(engine.total_realized_pnl, 2)
    assert round(engine.total_fees_paid, 2) == 0.80  # 0.20 + 0.20 + 0.22 + 0.18


@pytest.mark.asyncio
async def test_reconciliation_detects_all_discrepancies():
    """
    Tests ReconciliationEngine detecting:
    1. Cash balance drift
    2. Missing protective stop order
    3. Position quantity mismatch
    """
    mock_client = AsyncMock()
    # Mock open orders: only ONE limit order, NO stop loss order
    mock_client.fetch_open_orders.return_value = [
        {"id": "ord_101", "symbol": "BTC/USDT", "side": "buy", "type": "limit", "amount": 0.05}
    ]
    # Mock balances: free USDT is 4000, BTC is 0.04 (mismatch with local 0.05)
    mock_client.fetch_balance.return_value = {
        "USDT": {"free": 4000.0, "used": 0.0, "total": 4000.0},
        "total": {"BTC": 0.04, "USDT": 4000.0},
    }

    mock_engine = MagicMock()
    mock_engine.available_balance = 4500.0  # $500 balance drift!

    reconciler = ReconciliationEngine(execution_engine=mock_engine)

    local_positions = {
        "BTC/USDT": MagicMock(quantity=0.05, stop_loss=59000.0)
    }
    local_orders = {}

    report = await reconciler.reconcile_orders_and_positions(
        local_open_positions=local_positions,
        local_orders=local_orders,
        exchange_client=mock_client,
    )

    assert not report.is_synchronized
    disc_types = [d.discrepancy_type for d in report.discrepancies]
    assert "BALANCE_DRIFT" in disc_types
    assert "MISSING_PROTECTIVE_STOP" in disc_types
    assert "POSITION_QUANTITY_MISMATCH" in disc_types


@pytest.mark.asyncio
async def test_live_binance_execution_partial_fill_and_fail_closed():
    """
    Tests BinanceLiveExecutionEngine:
    1. Handles partial fill correctly (stop order matches executedQty, not requested qty)
    2. Enforces fail-closed: if protective stop order is rejected, position is emergency liquidated.
    """
    from shared.config import get_settings
    settings = get_settings()

    mock_client = AsyncMock()
    # Market buy returns partial fill: requested 0.1 BTC, filled only 0.05 BTC
    mock_client.create_order.side_effect = [
        # 1. Market order response (partial fill)
        {
            "id": "112233",
            "status": "PARTIALLY_FILLED",
            "filled": 0.05,
            "price": 60000.0,
            "average": 60000.0,
            "fee": {"cost": 3.0},
        },
        # 2. Protective stop order failure
        Exception("Binance 400: Filter failure STOP_PRICE_INVALID")
    ]

    # Temporarily set live trading flags for mock engine init
    old_live = settings.LIVE_TRADING
    old_armed = settings.LIVE_TRADING_ARMED
    settings.LIVE_TRADING = True
    settings.LIVE_TRADING_ARMED = True

    try:
        live_engine = BinanceLiveExecutionEngine(
            api_key="mock_key",
            api_secret="mock_secret",
            armed=True,
            client=mock_client,
        )

        decision = RiskDecision(
            approved=True,
            symbol="BTC/USDT",
            direction=SignalDirection.LONG,
            calculated_size=0.1,
            entry_price=60000.0,
            stop_loss=58000.0,
            take_profit=65000.0,
            risk_amount=200.0,
        )

        # Protective stop failure should raise RuntimeError and call emergency liquidation
        with pytest.raises(RuntimeError, match="FAIL-CLOSED INVARIANT"):
            await live_engine.submit_order(decision, strategy_name="test_strat")

        # Verify emergency liquidation was called with the PARTIAL fill qty (0.05), NOT requested qty (0.1)
        assert mock_client.create_order.call_count == 3
        liquidation_call = mock_client.create_order.call_args_list[2]
        assert liquidation_call.kwargs["type"] == "market"
        assert liquidation_call.kwargs["side"] == "sell"
        assert liquidation_call.kwargs["amount"] == 0.05

    finally:
        settings.LIVE_TRADING = old_live
        settings.LIVE_TRADING_ARMED = old_armed
