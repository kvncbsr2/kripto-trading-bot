from datetime import datetime, timedelta, timezone

import pytest

from services.execution.paper_execution import PaperExecutionEngine
from shared.enums import PositionSide, PositionStatus
from shared.schemas import Position


def test_daily_pnl_distinguishes_previous_days_realized_pnl():
    """Verifies that trades closed on previous days are excluded from current daily_pnl (AUDIT-04)."""
    engine = PaperExecutionEngine(initial_balance=5000.0)

    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)

    # Position closed yesterday with +$100 profit
    old_pos = Position(
        position_id="pos_old",
        symbol="BTC/USDT",
        side=PositionSide.LONG,
        entry_price=50000.0,
        quantity=0.1,
        current_price=51000.0,
        stop_loss=49000.0,
        take_profit=52000.0,
        realized_pnl=100.0,
        status=PositionStatus.CLOSED,
        opened_at=yesterday - timedelta(hours=2),
        closed_at=yesterday,
    )
    engine.closed_positions_history.append(old_pos)

    # Lifetime realized PnL should reflect the $100
    assert engine.total_realized_pnl == 100.0

    # But today's daily realized PnL and daily PnL must be 0.0
    assert engine.daily_realized_pnl == 0.0
    assert engine.daily_pnl == 0.0

    # Now close a position today with +$50 profit
    today_pos = Position(
        position_id="pos_today",
        symbol="ETH/USDT",
        side=PositionSide.LONG,
        entry_price=3000.0,
        quantity=1.0,
        current_price=3050.0,
        stop_loss=2900.0,
        take_profit=3200.0,
        realized_pnl=50.0,
        status=PositionStatus.CLOSED,
        opened_at=now - timedelta(minutes=30),
        closed_at=now,
    )
    engine.closed_positions_history.append(today_pos)

    # Now lifetime is $150, but daily is strictly $50
    assert engine.total_realized_pnl == 150.0
    assert engine.daily_realized_pnl == 50.0
    assert engine.daily_pnl == 50.0
