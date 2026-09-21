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


def test_persisted_sqlite_paper_account_matches_ledger_invariants():
    """
    P0 Authoritative Ledger Equality Invariant:
    Verifies that paper_account in SQLite strictly satisfies:
    1. balance == initial_balance + sum(closed.realized_pnl) - sum(open.fees_paid)
    2. available_balance == balance - reserved_balance
    3. reserved_balance == sum(open.entry_price * quantity)
    """
    import sqlite3
    from pathlib import Path
    db_path = Path(__file__).resolve().parents[2] / "kripto_agent.db"
    if not db_path.exists():
        pytest.skip("kripto_agent.db does not exist in repo root")

    conn = sqlite3.connect(str(db_path))
    c = conn.cursor()
    c.execute("SELECT initial_balance, balance, available_balance, reserved_balance FROM paper_account WHERE id=1")
    row = c.fetchone()
    assert row is not None
    init_bal, bal, avail, res = row

    c.execute("SELECT sum(realized_pnl) FROM paper_positions WHERE status='CLOSED'")
    closed_pnl = float(c.fetchone()[0] or 0.0)

    c.execute("SELECT sum(fees_paid), sum(entry_price * quantity), sum(partial_realized_pnl) FROM paper_positions WHERE status='OPEN'")
    open_fees, open_reserved, open_partial_pnl = c.fetchone()
    open_fees = float(open_fees or 0.0)
    open_reserved = float(open_reserved or 0.0)
    open_partial_pnl = float(open_partial_pnl or 0.0)
    conn.close()

    expected_bal = round(init_bal + closed_pnl + open_partial_pnl - open_fees, 2)
    expected_res = round(open_reserved, 2)
    expected_avail = round(expected_bal - expected_res, 2)

    assert round(bal, 2) == expected_bal, f"Balance drift: DB balance {bal} != expected {expected_bal}"
    assert round(res, 2) == expected_res, f"Reserved drift: DB reserved {res} != expected {expected_res}"
    assert round(avail, 2) == expected_avail, f"Available drift: DB available {avail} != expected {expected_avail}"

