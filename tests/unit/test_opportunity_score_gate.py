import pytest
from datetime import datetime, timezone

from services.risk_engine.risk_engine import RiskEngine
from shared.enums import SignalDirection
from shared.schemas import PortfolioState, Signal


def test_opportunity_score_hard_gate():
    risk_engine = RiskEngine()
    portfolio = PortfolioState(
        balance=5000.0,
        available_balance=5000.0,
        equity=5000.0,
        daily_pnl=0.0,
    )
    now = datetime.now(timezone.utc)

    # 1. Signal with opportunity_score = 49.0 -> MUST BE REJECTED
    sig_49 = Signal(
        symbol="BTC/USDT",
        timestamp=now,
        strategy="r10_rsi_divergence",
        direction=SignalDirection.LONG,
        entry_price=50000.0,
        stop_price=49000.0,
        take_profit=52000.0,
        opportunity_score=49.0,
    )
    decision_49 = risk_engine.evaluate_signal(sig_49, portfolio, latest_market_time=now)
    assert not decision_49.approved
    assert "REJECTED_LOW_OPPORTUNITY_SCORE" in decision_49.reason
    assert "49.0" in decision_49.reason

    # 2. Signal with opportunity_score = 50.0 -> MUST PASS GATE
    sig_50 = Signal(
        symbol="BTC/USDT",
        timestamp=now,
        strategy="r10_rsi_divergence",
        direction=SignalDirection.LONG,
        entry_price=50000.0,
        stop_price=49000.0,
        take_profit=52000.0,
        opportunity_score=50.0,
    )
    decision_50 = risk_engine.evaluate_signal(sig_50, portfolio, latest_market_time=now)
    assert decision_50.approved, f"Expected approved, got: {decision_50.reason}"

    # 3. Signal with opportunity_score = 51.0 -> MUST PASS GATE
    sig_51 = Signal(
        symbol="ETH/USDT",
        timestamp=now,
        strategy="r10_rsi_divergence",
        direction=SignalDirection.LONG,
        entry_price=3000.0,
        stop_price=2900.0,
        take_profit=3200.0,
        opportunity_score=51.0,
    )
    decision_51 = risk_engine.evaluate_signal(sig_51, portfolio, latest_market_time=now)
    assert decision_51.approved, f"Expected approved, got: {decision_51.reason}"

    # 4. Signal with score in metadata["opportunity_score"] = 45.0 -> REJECTED
    sig_meta_45 = Signal(
        symbol="SOL/USDT",
        timestamp=now,
        strategy="r10_rsi_divergence",
        direction=SignalDirection.LONG,
        entry_price=100.0,
        stop_price=95.0,
        take_profit=110.0,
        metadata={"opportunity_score": 45.0},
    )
    decision_meta_45 = risk_engine.evaluate_signal(sig_meta_45, portfolio, latest_market_time=now)
    assert not decision_meta_45.approved
    assert "REJECTED_LOW_OPPORTUNITY_SCORE" in decision_meta_45.reason
