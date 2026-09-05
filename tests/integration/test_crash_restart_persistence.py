from services.execution.paper_execution import PaperExecutionEngine
from shared.enums import PositionStatus, SignalDirection
from shared.schemas import RiskDecision


def test_crash_restart_persistence_lifecycle(tmp_path):
    """
    P0-4 Verification:
    START -> OPEN POSITION -> CRASH -> RESTART -> RESTORE -> CONTINUE -> CLOSE
    """
    db_file = tmp_path / "paper_crash_test.db"

    # Step 1: Initial Start
    engine1 = PaperExecutionEngine(
        initial_balance=5000.0,
        maker_fee=0.001,
        taker_fee=0.001,
        slippage_bps=5.0,
        db_path=str(db_file),
    )
    assert engine1.balance == 5000.0
    assert len(engine1.open_positions) == 0

    # Step 2: Open Position
    decision = RiskDecision(
        approved=True,
        symbol="BTC/USDT",
        direction=SignalDirection.LONG,
        calculated_size=0.05,
        entry_price=60000.0,
        stop_loss=58000.0,
        take_profit=64000.0,
        reason="Approved signal",
    )
    order1, fill1, pos1 = engine1.execute_market_order(decision, strategy_name="r10_rsi_divergence")
    assert "BTC/USDT" in engine1.open_positions
    assert len(engine1.orders) == 1
    assert len(engine1.fills) == 1
    saved_balance = engine1.balance
    saved_available = engine1.available_balance
    saved_reserved = engine1.reserved_balance
    saved_order_id = order1.order_id

    # Step 3: CRASH (Drop engine1 from memory)
    del engine1

    # Step 4: RESTART (New engine pointing to same SQLite DB)
    engine2 = PaperExecutionEngine(
        initial_balance=5000.0,
        maker_fee=0.001,
        taker_fee=0.001,
        slippage_bps=5.0,
        db_path=str(db_file),
    )

    # Step 5: RESTORE Verification
    assert engine2.balance == saved_balance
    assert engine2.available_balance == saved_available
    assert engine2.reserved_balance == saved_reserved
    assert "BTC/USDT" in engine2.open_positions
    restored_pos = engine2.open_positions["BTC/USDT"]
    assert restored_pos.status == PositionStatus.OPEN
    assert restored_pos.quantity == 0.05
    assert restored_pos.entry_price == pos1.entry_price
    assert saved_order_id in engine2.orders
    assert len(engine2.fills) == 1

    # Step 6: CONTINUE Operations (Close position at profit)
    closed = engine2.close_position("BTC/USDT", exit_price=63000.0, reason="TAKE_PROFIT")
    assert closed is not None
    assert closed.status == PositionStatus.CLOSED
    assert closed.realized_pnl > 0
    assert len(engine2.open_positions) == 0
    assert len(engine2.closed_positions_history) == 1
    assert engine2.reserved_balance == 0.0
    post_close_balance = engine2.balance

    # Step 7: Second Crash & Restart Verification
    del engine2
    engine3 = PaperExecutionEngine(
        initial_balance=5000.0,
        maker_fee=0.001,
        taker_fee=0.001,
        slippage_bps=5.0,
        db_path=str(db_file),
    )
    assert engine3.balance == post_close_balance
    assert len(engine3.open_positions) == 0
    assert len(engine3.closed_positions_history) == 1
    assert engine3.total_realized_pnl == closed.realized_pnl
    assert len(engine3.fills) == 2
