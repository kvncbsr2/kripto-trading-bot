"""
Unit tests for 50% Partial Exit at +1.0R and Break-Even Stop adjustment.
"""
from datetime import datetime, timedelta, timezone

from services.execution.paper_execution import PaperExecutionEngine
from shared.schemas import Position, PositionSide, PositionStatus, RiskDecision, SignalDirection


def test_partial_exit_at_1r_and_pullback_to_breakeven_zero_fees():
    """
    Verifies that when price reaches +1.0R:
    - 50% of the position is closed.
    - Profit is banked into cash balance.
    - Stop loss is moved to break-even.
    - A subsequent pullback to entry price closes the remaining 50% at BE,
      leaving the total trade net profitable instead of zero/negative.
    """
    engine = PaperExecutionEngine(
        initial_balance=1000.0,
        maker_fee=0.0,
        taker_fee=0.0,
        slippage_bps=0.0,
        enable_trailing_stop=True,
        enable_partial_exit=True,
        is_spot_mode=True,
        db_path=":memory:",
    )

    # 1. Open LONG position: 2.0 units @ $100. SL=$90 (risk=$10, 1R=$110), TP=$130 (+3R)
    decision = RiskDecision(
        approved=True,
        symbol="BTC/USDT",
        direction=SignalDirection.LONG,
        calculated_size=2.0,
        entry_price=100.0,
        stop_loss=90.0,
        take_profit=130.0,
        risk_amount=20.0,
    )
    order, fill, pos = engine.execute_market_order(decision, strategy_name="test_partial")

    assert pos.quantity == 2.0
    assert pos.entry_price == 100.0
    assert pos.initial_stop_loss == 90.0
    assert pos.risk_dist == 10.0
    assert pos.partial_tp_hit is False
    assert engine.reserved_balance == 200.0
    assert engine.available_balance == 800.0
    assert engine.balance == 1000.0

    # 2. Candle 1: High reaches $110 (+1.0R target), Low is $105, Close is $108
    exit_event = engine.check_position_stops_and_targets(
        symbol="BTC/USDT",
        high=110.0,
        low=105.0,
        close=108.0,
    )

    # Position is NOT fully closed yet, so exit_event is None
    assert exit_event is None
    assert "BTC/USDT" in engine.open_positions
    pos = engine.open_positions["BTC/USDT"]

    # 50% closed: quantity is now 1.0, partial_tp_hit is True
    assert pos.quantity == 1.0
    assert pos.partial_tp_hit is True
    assert pos.partial_realized_at is not None
    assert pos.partial_realized_pnl == 10.0  # 1.0 qty * ($110 - $100)
    assert pos.stop_loss == 100.0  # Moved to Break-Even!

    # Cash proceeds released: $100 principal + $10 profit = $110 returned
    assert engine.reserved_balance == 100.0
    assert engine.available_balance == 910.0
    assert engine.balance == 1010.0
    assert engine.total_realized_pnl == 10.0

    # 3. Candle 2: Pullback happens! Price drops to $100 (hitting Break-Even SL)
    exit_event = engine.check_position_stops_and_targets(
        symbol="BTC/USDT",
        high=106.0,
        low=100.0,
        close=100.0,
    )

    assert exit_event is not None
    closed_pos, reason, exit_price = exit_event
    assert reason == "STOP_LOSS"
    assert exit_price == 100.0
    assert closed_pos.status == PositionStatus.CLOSED

    # Total realized PnL of position is +$10 (first leg +$10, second leg $0)
    assert closed_pos.realized_pnl == 10.0
    assert closed_pos.quantity == 2.0  # Full lifecycle quantity preserved
    assert engine.reserved_balance == 0.0
    assert engine.available_balance == 1010.0
    assert engine.balance == 1010.0
    assert engine.equity == 1010.0
    assert engine.total_realized_pnl == 10.0

    # Fills: 1 Buy + 1 Partial Sell + 1 Final Sell = 3 fills
    assert len(engine.fills) == 3
    assert engine.fills[0].quantity == 2.0
    assert engine.fills[1].quantity == 1.0
    assert engine.fills[1].price == 110.0
    assert engine.fills[2].quantity == 1.0
    assert engine.fills[2].price == 100.0


def test_partial_exit_with_fees_and_trailing():
    """
    Verifies partial exit + trailing continuation with standard taker fees (0.1%).
    """
    engine = PaperExecutionEngine(
        initial_balance=5000.0,
        maker_fee=0.001,
        taker_fee=0.001,
        slippage_bps=0.0,
        enable_trailing_stop=True,
        enable_partial_exit=True,
        is_spot_mode=True,
        db_path=":memory:",
    )

    decision = RiskDecision(
        approved=True,
        symbol="ETH/USDT",
        direction=SignalDirection.LONG,
        calculated_size=1.0,
        entry_price=1000.0,
        stop_loss=950.0,  # 1R = $50 -> +1R = $1050
        take_profit=1200.0,
        risk_amount=50.0,
    )
    order, fill, pos = engine.execute_market_order(decision, strategy_name="test_eth")
    # Entry fee: 1.0 * 1000 * 0.001 = $1.00
    assert fill.fee == 1.00
    assert engine.balance == 4999.0

    # 1. Price hits +1.0R ($1050)
    engine.check_position_stops_and_targets(
        symbol="ETH/USDT",
        high=1050.0,
        low=1020.0,
        close=1045.0,
    )

    pos = engine.open_positions["ETH/USDT"]
    assert pos.quantity == 0.5
    assert pos.partial_tp_hit is True
    assert pos.stop_loss == 1000.0

    # 2. Price continues running to $1150 (peak lock trails stop higher to $1075)
    engine.check_position_stops_and_targets(
        symbol="ETH/USDT",
        high=1150.0,
        low=1100.0,
        close=1140.0,
    )

    pos = engine.open_positions["ETH/USDT"]
    # Peak gain = 1150 - 1000 = 150. Stop raised to peak - 50% = 1150 - 75 = 1075.0
    assert pos.stop_loss == 1075.0

    # 3. Pullback hits trailing stop @ $1075.0
    exit_event = engine.check_position_stops_and_targets(
        symbol="ETH/USDT",
        high=1140.0,
        low=1075.0,
        close=1075.0,
    )

    assert exit_event is not None
    closed_pos, reason, exit_price = exit_event
    assert reason == "STOP_LOSS"
    assert exit_price == 1075.0

    # Both legs were closed in profit! Total realized PnL must be positive and balance > $5000
    assert closed_pos.realized_pnl > 50.0
    assert engine.balance > 5050.0
    assert round(engine.balance - 5000.0, 2) == round(closed_pos.realized_pnl, 2)


def test_partial_exit_sqlite_restoration(tmp_path):
    """
    Verifies that a position with a partial exit is accurately saved and restored from SQLite.
    """
    db_file = str(tmp_path / "test_partial_persist.db")
    engine1 = PaperExecutionEngine(
        initial_balance=10000.0,
        maker_fee=0.0,
        taker_fee=0.0,
        slippage_bps=0.0,
        enable_trailing_stop=True,
        enable_partial_exit=True,
        db_path=db_file,
    )

    decision = RiskDecision(
        approved=True,
        symbol="SOL/USDT",
        direction=SignalDirection.LONG,
        calculated_size=10.0,
        entry_price=100.0,
        stop_loss=90.0,  # 1R = $10 -> +1R = $110
        take_profit=150.0,
        risk_amount=100.0,
    )
    engine1.execute_market_order(decision, strategy_name="sol_strat")

    # Reach +1R
    engine1.check_position_stops_and_targets("SOL/USDT", high=110.0, low=105.0, close=109.0)

    pos1 = engine1.open_positions["SOL/USDT"]
    assert pos1.quantity == 5.0
    assert pos1.partial_tp_hit is True
    assert pos1.stop_loss == 100.0

    # Simulate crash & restart: Create engine2 reading same DB
    engine2 = PaperExecutionEngine(
        initial_balance=10000.0,
        maker_fee=0.0,
        taker_fee=0.0,
        slippage_bps=0.0,
        enable_trailing_stop=True,
        enable_partial_exit=True,
        db_path=db_file,
    )

    assert "SOL/USDT" in engine2.open_positions
    pos2 = engine2.open_positions["SOL/USDT"]
    assert pos2.quantity == 5.0
    assert pos2.partial_tp_hit is True
    assert pos2.partial_realized_pnl == 50.0  # 5.0 * $10
    assert pos2.partial_realized_at == pos1.partial_realized_at
    assert pos2.stop_loss == 100.0
    assert engine2.balance == engine1.balance
    assert engine2.available_balance == engine1.available_balance
    assert engine2.reserved_balance == engine1.reserved_balance


def test_previous_day_partial_profit_is_not_counted_today():
    engine = PaperExecutionEngine(initial_balance=5000.0, db_path=":memory:")
    position = Position(
        position_id="old_partial", symbol="BTC/USDT", side=PositionSide.LONG,
        quantity=0.01, entry_price=100.0, current_price=100.0,
        stop_loss=95.0, take_profit=110.0, partial_tp_hit=True,
        partial_realized_pnl=50.0,
        partial_realized_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    engine.open_positions[position.symbol] = position
    assert engine.daily_realized_pnl == 0.0
