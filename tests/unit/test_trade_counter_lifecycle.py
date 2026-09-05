from datetime import datetime, timezone

import pytest

from services.execution.order_manager import OrderManager
from services.execution.paper_execution import PaperExecutionEngine
from services.risk_engine.risk_engine import RiskEngine
from shared.enums import SignalDirection
from shared.schemas import PortfolioState, Signal


def test_trade_counter_not_incremented_on_risk_approval():
    """Verifies that evaluating and approving a signal does NOT increment the trade counter (AUDIT-05)."""
    risk_engine = RiskEngine()
    portfolio = PortfolioState(balance=5000.0, equity=5000.0)

    signal = Signal(
        symbol="BTC/USDT",
        strategy="r10_rsi_divergence",
        direction=SignalDirection.LONG,
        entry_price=60000.0,
        stop_price=59000.0,
        take_profit=62000.0,
        timestamp=datetime.now(timezone.utc),
    )

    initial_trade_count = risk_engine.circuit_breaker.trades_today_count
    decision = risk_engine.evaluate_signal(signal=signal, portfolio=portfolio)

    assert decision.approved is True
    # Count must remain unchanged prior to execution
    assert risk_engine.circuit_breaker.trades_today_count == initial_trade_count


@pytest.mark.asyncio
async def test_trade_counter_incremented_on_order_fill():
    """Verifies trade counter increments strictly when the order is filled via OrderManager."""
    risk_engine = RiskEngine()
    paper_broker = PaperExecutionEngine(initial_balance=5000.0)
    order_mgr = OrderManager(execution_engine=paper_broker, risk_engine=risk_engine)

    signal = Signal(
        symbol="BTC/USDT",
        strategy="r10_rsi_divergence",
        direction=SignalDirection.LONG,
        entry_price=60000.0,
        stop_price=59000.0,
        take_profit=62000.0,
        timestamp=datetime.now(timezone.utc),
    )

    decision = risk_engine.evaluate_signal(
        signal=signal,
        portfolio=paper_broker.to_portfolio_state(),
    )
    assert decision.approved is True
    assert risk_engine.circuit_breaker.trades_today_count == 0

    # Execute order through OrderManager
    order, fill, pos = await order_mgr.execute_risk_decision(
        decision=decision,
        strategy_name="r10_rsi_divergence",
    )

    # Now trade count MUST be incremented
    assert risk_engine.circuit_breaker.trades_today_count == 1
