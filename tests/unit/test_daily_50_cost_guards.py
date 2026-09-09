from datetime import datetime, timezone

import pytest

from services.risk_engine.position_sizing import calculate_atr_position_size
from services.risk_engine.risk_engine import RiskEngine, settings
from shared.enums import PositionSide, SignalDirection
from shared.schemas import PortfolioState, Position, Signal


@pytest.fixture(autouse=True)
def costs(monkeypatch):
    monkeypatch.setattr(settings, "TAKER_FEE", 0.001)
    monkeypatch.setattr(settings, "MAKER_FEE", 0.001)
    monkeypatch.setattr(settings, "SLIPPAGE_BPS", 5.0)
    monkeypatch.setattr(settings, "MIN_NET_RISK_REWARD", 1.5)


def signal(stop=98.0, target=104.0):
    return Signal(symbol="BTC/USDT", strategy="audit", direction=SignalDirection.LONG,
                  entry_price=100.0, stop_price=stop, take_profit=target)


def test_gross_two_to_one_rejected_when_costs_destroy_edge():
    decision = RiskEngine().evaluate_signal(
        signal(stop=99.0, target=102.0), PortfolioState(balance=5000, equity=5000)
    )
    assert not decision.approved
    assert "NET_RISK_REWARD" in decision.reason


def test_approved_stop_loss_includes_actual_round_trip_costs():
    decision = RiskEngine().evaluate_signal(signal(), PortfolioState(balance=5000, equity=5000))
    assert decision.approved
    entry_fill, exit_fill = 100 * 1.0005, 98 * 0.9995
    net_loss = decision.calculated_size * (entry_fill - exit_fill + (entry_fill + exit_fill) * 0.001)
    assert net_loss <= 25
    assert decision.risk_amount == pytest.approx(net_loss, abs=0.01)


def test_fifty_dollar_target_blocks_new_entries():
    engine = RiskEngine()
    assert engine.evaluate_signal(signal(), PortfolioState(balance=5049, equity=5049, daily_pnl=49)).approved
    decision = engine.evaluate_signal(signal(), PortfolioState(balance=5050, equity=5050, daily_pnl=50))
    assert not decision.approved
    assert "Hard daily profit target" in decision.reason


def test_configured_daily_trade_cap_reaches_circuit_breaker():
    engine = RiskEngine(max_trades_per_day=2)
    engine.record_executed_trade()
    engine.record_executed_trade()
    decision = engine.evaluate_signal(signal(), PortfolioState(balance=5000, equity=5000))
    assert not decision.approved
    assert "2/2" in decision.reason


def test_unrealized_profit_does_not_count_as_achieved_daily_target():
    portfolio = PortfolioState(balance=5000, equity=5060, daily_pnl=60,
                               unrealized_pnl=60, daily_realized_pnl=0)
    assert RiskEngine().evaluate_signal(signal(), portfolio).approved


def test_realized_target_blocks_entries_even_when_open_positions_lose():
    portfolio = PortfolioState(balance=5050, equity=5040, daily_pnl=40,
                               unrealized_pnl=-10, daily_realized_pnl=50)
    assert not RiskEngine().evaluate_signal(signal(), portfolio).approved


def test_new_position_respects_remaining_daily_loss_budget():
    portfolio = PortfolioState(balance=4955, equity=4955, daily_pnl=-45)
    decision = RiskEngine().evaluate_signal(signal(), portfolio)
    assert decision.approved
    assert 0 < decision.risk_amount <= 5


def test_open_position_stop_risk_is_reserved():
    position = Position(position_id="existing", symbol="ETH/USDT", side=PositionSide.LONG,
                        entry_price=100, current_price=100, quantity=10, stop_loss=95,
                        take_profit=110)
    portfolio = PortfolioState(balance=4000, equity=5000, open_positions=[position])
    decision = RiskEngine().evaluate_signal(signal(), portfolio)
    assert not decision.approved
    assert "DAILY_RISK_BUDGET" in decision.reason


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1.0])
def test_invalid_costs_cannot_produce_position(value):
    assert calculate_atr_position_size(5000, 100, 98, fee_rate=value) == (0.0, 0.0)


def test_daily_trade_lock_resets_on_next_utc_day():
    engine = RiskEngine(max_trades_per_day=1)
    engine.circuit_breaker.record_trade(datetime(2026, 9, 9, 23, tzinfo=timezone.utc))
    fresh_signal = signal()
    fresh_signal.timestamp = datetime(2026, 9, 10, 0, 0, tzinfo=timezone.utc)
    decision = engine.evaluate_signal(
        fresh_signal, PortfolioState(balance=5000, equity=5000),
        current_time=datetime(2026, 9, 10, 0, 1, tzinfo=timezone.utc),
    )
    assert decision.approved
    assert engine.circuit_breaker.trades_today_count == 0
