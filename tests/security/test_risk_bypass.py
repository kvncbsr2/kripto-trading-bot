import pytest

from services.execution.paper_execution import PaperExecutionEngine
from services.risk_engine.risk_engine import RiskEngine
from shared.enums import SignalDirection
from shared.schemas import PortfolioState, RiskDecision, Signal


def test_unapproved_risk_decision_rejected():
    broker = PaperExecutionEngine(initial_balance=5000.0)

    # Attempting to execute an unapproved RiskDecision must be rejected
    unapproved_decision = RiskDecision(
        approved=False,
        symbol="BTC/USDT",
        direction=SignalDirection.LONG,
        calculated_size=0.01,
        entry_price=60000.0,
        stop_loss=58800.0,
        take_profit=62400.0,
        risk_amount=20.0,
        reason="Exceeded daily risk budget",
    )

    with pytest.raises((ValueError, PermissionError), match="requires approved RiskDecision"):
        broker.execute_market_order(decision=unapproved_decision)


def test_risk_engine_veto_authority():
    risk_engine = RiskEngine(daily_max_loss_usd=50.0, max_open_positions=2)

    portfolio = PortfolioState(
        balance=5000.0,
        equity=4940.0,
        unrealized_pnl=0.0,
        realized_pnl=-60.0,
        daily_pnl=-60.0,  # exceeds daily max loss of $50
    )

    signal = Signal(
        symbol="BTC/USDT",
        strategy="R10_DIVERGENCE",
        direction=SignalDirection.LONG,
        entry_price=60000.0,
        stop_price=59000.0,
        take_profit=62000.0,
    )

    decision = risk_engine.evaluate_signal(signal, portfolio)
    assert not decision.approved
    assert "DAILY_RISK_LOCK" in decision.reason or "Daily loss reached" in decision.reason
