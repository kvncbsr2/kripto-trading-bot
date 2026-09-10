import pytest

from services.execution.paper_execution import PaperExecutionEngine
from shared.enums import SignalDirection
from shared.schemas import RiskDecision


def test_paper_execution_ledger_invariants():
    engine = PaperExecutionEngine(initial_balance=5000.0, taker_fee=0.001, slippage_bps=5.0)

    # Initial state invariants
    assert engine.balance == 5000.0
    assert engine.available_balance == 5000.0
    assert engine.reserved_balance == 0.0
    assert engine.equity == 5000.0
    assert engine.total_realized_pnl == 0.0
    assert engine.total_unrealized_pnl == 0.0
    assert len(engine.open_positions) == 0

    # 1. Execute Long trade
    decision = RiskDecision(
        approved=True,
        symbol="ETH/USDT",
        direction=SignalDirection.LONG,
        calculated_size=1.0,
        entry_price=3000.0,
        stop_loss=2900.0,
        take_profit=3200.0,
        risk_amount=25.0,
    )
    order, fill, pos = engine.execute_market_order(decision)

    # Invariant: balance >= 0, realized_pnl is still 0 (only open position)
    assert engine.balance >= 0.0
    assert engine.total_realized_pnl == 0.0
    assert len(engine.open_positions) == 1
    assert engine.equity == round(engine.balance + engine.total_unrealized_pnl, 2)

    # 2. Update market price upward -> unrealized PnL increases, realized PnL still 0
    engine.update_market_price("ETH/USDT", 3100.0)
    assert engine.total_unrealized_pnl > 0.0
    assert engine.total_realized_pnl == 0.0
    assert engine.equity == round(engine.balance + engine.total_unrealized_pnl, 2)

    # 3. Close position -> realized PnL realized, unrealized becomes 0
    closed_pos = engine.close_position("ETH/USDT", exit_price=3150.0, reason="MANUAL_CLOSE")
    assert closed_pos is not None
    assert closed_pos.realized_pnl > 0.0
    assert engine.total_realized_pnl == round(closed_pos.realized_pnl, 2)
    assert engine.total_unrealized_pnl == 0.0
    assert engine.equity == round(engine.balance, 2)
    assert engine.balance > 5000.0
    assert len(engine.open_positions) == 0


def test_paper_execution_spot_short_rejection():
    engine = PaperExecutionEngine(initial_balance=5000.0, is_spot_mode=True)
    short_decision = RiskDecision(
        approved=True,
        symbol="BTC/USDT",
        direction=SignalDirection.SHORT,
        calculated_size=0.01,
        entry_price=60000.0,
        stop_loss=61000.0,
        take_profit=58000.0,
        risk_amount=20.0,
    )
    with pytest.raises(ValueError, match="Spot mode does not support SHORT"):
        engine.execute_market_order(short_decision)
