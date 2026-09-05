import pytest

from services.execution.paper_execution import PaperExecutionEngine
from services.risk_engine.risk_engine import RiskEngine
from shared.enums import SignalDirection
from shared.schemas import PortfolioState, RiskDecision, Signal


def test_risk_engine_spot_short_rejection():
    risk_engine = RiskEngine(is_spot_mode=True)

    portfolio = PortfolioState(balance=5000.0, equity=5000.0)

    short_signal = Signal(
        symbol="BTC/USDT",
        strategy="R10_DIVERGENCE",
        direction=SignalDirection.SHORT,
        entry_price=64000.0,
        stop_price=65000.0,
        take_profit=62000.0,
    )

    decision = risk_engine.evaluate_signal(short_signal, portfolio)
    assert not decision.approved
    assert "Spot Mode: SHORT is NOT_SUPPORTED (SIGNAL_ONLY)" in decision.reason


def test_paper_execution_spot_short_rejection():
    broker = PaperExecutionEngine(initial_balance=5000.0, is_spot_mode=True)

    forced_decision = RiskDecision(
        approved=True,
        symbol="ETH/USDT",
        direction=SignalDirection.SHORT,
        calculated_size=1.0,
        entry_price=2500.0,
        stop_loss=2600.0,
        take_profit=2300.0,
    )

    with pytest.raises(ValueError, match="Spot mode does not support SHORT execution"):
        broker.execute_market_order(decision=forced_decision)
