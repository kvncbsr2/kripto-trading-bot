from services.risk_engine.circuit_breaker import CircuitBreaker, CircuitState
from services.risk_engine.position_sizing import calculate_atr_position_size
from services.risk_engine.risk_engine import RiskEngine
from services.risk_engine.stop_loss import validate_stop_and_target
from shared.enums import SignalDirection
from shared.schemas import PortfolioState, Signal


def test_position_sizing_atr():
    equity = 5000.0
    entry = 60000.0
    stop = 59000.0  # $1,000 stop distance
    # Risk per trade 0.5% of 5,000 = $25.00
    # Expected size = 25 / 1000 = 0.025 BTC
    size, risk_amount = calculate_atr_position_size(
        equity=equity,
        entry_price=entry,
        stop_price=stop,
        risk_per_trade=0.005,
        max_position_equity_ratio=0.5,
    )
    assert risk_amount == 25.00
    assert size == 0.025


def test_stop_and_target_validation():
    # Valid Long R:R = (62000 - 60000) / (60000 - 59000) = 2.0
    assert validate_stop_and_target(SignalDirection.LONG, 60000.0, 59000.0, 62000.0, 1.5)
    # Invalid Long: Stop above entry
    assert not validate_stop_and_target(SignalDirection.LONG, 60000.0, 61000.0, 62000.0, 1.5)
    # Invalid Long: Target below entry
    assert not validate_stop_and_target(SignalDirection.LONG, 60000.0, 59000.0, 59500.0, 1.5)
    # Valid Short R:R = (60000 - 58000) / (61000 - 60000) = 2.0
    assert validate_stop_and_target(SignalDirection.SHORT, 60000.0, 61000.0, 58000.0, 1.5)


def test_circuit_breaker_daily_loss_lock():
    cb = CircuitBreaker(daily_max_loss_usd=50.0)
    portfolio = PortfolioState(
        balance=4940.0,
        equity=4940.0,
        daily_pnl=-55.0,  # breached $50 daily loss
    )
    tripped, reason, event_type = cb.check(portfolio)
    assert tripped is True
    assert cb.state == CircuitState.LOCKED
    assert "DAILY_RISK_LOCK" in (reason or "")


def test_risk_engine_veto_on_max_positions():
    engine = RiskEngine(max_open_positions=1)
    from shared.enums import PositionSide
    from shared.schemas import Position

    portfolio = PortfolioState(
        balance=5000.0,
        equity=5000.0,
        open_positions=[
            Position(
                position_id="pos1",
                symbol="ETH/USDT",
                side=PositionSide.LONG,
                entry_price=3000.0,
                quantity=1.0,
                current_price=3000.0,
                stop_loss=2900.0,
                take_profit=3200.0,
            )
        ],
    )
    sig = Signal(
        symbol="BTC/USDT",
        strategy="trend_following",
        direction=SignalDirection.LONG,
        entry_price=60000.0,
        stop_price=59000.0,
        take_profit=62000.0,
    )
    decision = engine.evaluate_signal(sig, portfolio)
    assert decision.approved is False
    assert "Max open positions" in decision.reason
