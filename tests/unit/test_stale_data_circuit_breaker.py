from datetime import datetime, timedelta, timezone

import pytest

from services.risk_engine.circuit_breaker import CircuitBreaker, CircuitState
from services.risk_engine.risk_engine import RiskEngine
from shared.enums import SignalDirection
from shared.schemas import PortfolioState, Signal


def test_stale_data_trips_circuit_breaker():
    """Verifies that market data older than stale threshold triggers circuit breaker (AUDIT-02)."""
    cb = CircuitBreaker(stale_data_seconds=60)
    portfolio = PortfolioState(balance=5000.0, equity=5000.0)

    now = datetime.now(timezone.utc)
    stale_candle_time = now - timedelta(seconds=120)

    tripped, reason, event_type = cb.check(
        portfolio=portfolio,
        latest_candle_time=stale_candle_time,
        current_time=now,
    )

    assert tripped is True
    assert "Market data feed is stale" in reason
    assert cb.state == CircuitState.WARNING


def test_risk_engine_rejects_on_stale_market_data():
    """Verifies RiskEngine rejects signal evaluation if latest market time is stale."""
    engine = RiskEngine()
    portfolio = PortfolioState(balance=5000.0, equity=5000.0)

    now = datetime.now(timezone.utc)
    stale_market_time = now - timedelta(seconds=300)

    signal = Signal(
        symbol="BTC/USDT",
        strategy="r10_rsi_divergence",
        direction=SignalDirection.LONG,
        entry_price=60000.0,
        stop_price=59000.0,
        take_profit=62000.0,
        timestamp=now,
    )

    decision = engine.evaluate_signal(
        signal=signal,
        portfolio=portfolio,
        latest_market_time=stale_market_time,
        current_time=now,
    )

    assert decision.approved is False
    assert "Risk Engine Locked" in decision.reason or "stale" in decision.reason.lower()
