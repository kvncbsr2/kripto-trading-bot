from services.paper_broker.broker import PaperBroker
from shared.enums import SignalDirection
from shared.schemas import RiskDecision


def test_paper_broker_market_fill_and_slippage():
    broker = PaperBroker(initial_balance=5000.0, taker_fee=0.001, slippage_bps=5.0)
    decision = RiskDecision(
        approved=True,
        symbol="BTC/USDT",
        direction=SignalDirection.LONG,
        calculated_size=0.05,
        entry_price=60000.0,
        stop_loss=59000.0,
        take_profit=62000.0,
        risk_amount=25.0,
    )
    order, fill, pos = broker.execute_market_order(decision, strategy_name="trend_following")

    assert order.status == "FILLED"
    # Long buy fill price should be slightly higher than 60,000 due to slippage
    assert fill.price > 60000.0
    assert fill.fee > 0
    assert pos.symbol == "BTC/USDT"
    assert pos.quantity == 0.05
    assert len(broker.portfolio.positions) == 1


def test_paper_broker_stop_loss_hit():
    broker = PaperBroker(initial_balance=5000.0)
    decision = RiskDecision(
        approved=True,
        symbol="BTC/USDT",
        direction=SignalDirection.LONG,
        calculated_size=0.025,
        entry_price=60000.0,
        stop_loss=59000.0,
        take_profit=62000.0,
        risk_amount=25.0,
    )
    broker.execute_market_order(decision)

    # Price drops to 58,800 -> hits stop loss
    result = broker.check_position_stops_and_targets(
        "BTC/USDT", high=59500.0, low=58800.0, close=58900.0
    )
    assert result is not None
    closed_pos, reason, exit_price = result
    assert reason == "STOP_LOSS"
    assert exit_price == 59000.0
    assert closed_pos.realized_pnl < 0
    assert len(broker.portfolio.positions) == 0


def test_paper_broker_take_profit_hit():
    broker = PaperBroker(initial_balance=5000.0)
    decision = RiskDecision(
        approved=True,
        symbol="BTC/USDT",
        direction=SignalDirection.LONG,
        calculated_size=0.025,
        entry_price=60000.0,
        stop_loss=59000.0,
        take_profit=62000.0,
        risk_amount=25.0,
    )
    broker.execute_market_order(decision)

    # Price rallies to 62,500 -> hits take profit
    result = broker.check_position_stops_and_targets(
        "BTC/USDT", high=62500.0, low=60500.0, close=62100.0
    )
    assert result is not None
    closed_pos, reason, exit_price = result
    assert reason == "TAKE_PROFIT"
    assert exit_price == 62000.0
    assert closed_pos.realized_pnl > 0
    assert broker.portfolio.balance > 5000.0
