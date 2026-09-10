import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from services.autonomous_runner import AutonomousPaperTrader
from services.execution.paper_execution import PaperExecutionEngine
from shared.enums import PositionSide, PositionStatus, SignalDirection
from shared.schemas import Position, RiskDecision


def test_breakeven_stop_lock_never_loosens_existing_trailing_stop():
    """
    Verifies that when a partial exit triggers, it sets stop loss to entry price,
    but never lowers an already trailed stop.
    """
    engine = PaperExecutionEngine(
        initial_balance=5000.0,
        maker_fee=0.0,
        taker_fee=0.0,
        slippage_bps=0.0,
        enable_trailing_stop=True,
        enable_partial_exit=True,
        db_path=":memory:",
    )

    decision = RiskDecision(
        approved=True,
        symbol="SOL/USDT",
        direction=SignalDirection.LONG,
        calculated_size=10.0,
        entry_price=100.0,
        stop_loss=90.0,  # 1R = $10 -> +1R = $110
        take_profit=150.0,
        risk_amount=100.0,
    )
    engine.execute_market_order(decision, strategy_name="sol_test")

    pos = engine.open_positions["SOL/USDT"]
    # Manually simulate a scenario where stop was trailed to 105.0 before partial TP was registered
    pos.stop_loss = 105.0

    # Execute partial exit
    engine.execute_partial_exit(symbol="SOL/USDT", fraction=0.5, exit_price=110.0, reason="PARTIAL_TP_1R")

    # Stop loss should REMAIN at 105.0 and NOT be downgraded to 100.0!
    assert pos.stop_loss == 105.0
    assert pos.quantity == 5.0
    assert pos.partial_tp_hit is True
    assert pos.partial_realized_pnl == 50.0


@pytest.mark.asyncio
async def test_autonomous_runner_detects_partial_1r_exit_and_updates_state():
    """
    Verifies that when an open position's price reaches 1R, autonomous_runner's cycle:
    1. Triggers partial exit via broker.check_position_stops_and_targets.
    2. Detects was_partial -> partial_tp_hit transition.
    3. Updates last_action with 1R Partial TP message.
    4. Notifies telegram.
    """
    runner = AutonomousPaperTrader()
    runner.is_active = True

    pos = Position(
        position_id="pos_btc_1",
        symbol="BTC/USDT",
        side=PositionSide.LONG,
        entry_price=50000.0,
        quantity=0.1,
        current_price=50000.0,
        stop_loss=49000.0,  # 1R = $1000 -> +1R = $51000
        take_profit=54000.0,
        risk_dist=1000.0,
        status=PositionStatus.OPEN,
        opened_at=datetime.now(timezone.utc),
        partial_tp_hit=False,
    )

    mock_broker = MagicMock()
    mock_broker.open_positions = {"BTC/USDT": pos}
    mock_broker.closed_positions_history = []
    mock_broker.daily_realized_pnl = 0.0
    mock_broker.daily_pnl = 0.0
    mock_broker.balance = 5000.0
    mock_broker.equity = 5000.0

    def mock_check(symbol, high, low, close):
        # Simulate partial exit side effect
        pos.quantity = 0.05
        pos.partial_tp_hit = True
        pos.partial_realized_pnl = 50.0
        pos.stop_loss = 50000.0
        return None  # Full position not closed yet

    mock_broker.check_position_stops_and_targets.side_effect = mock_check

    mock_bus = MagicMock()
    mock_bus.broker = mock_broker
    mock_bus.runtime_state = {}
    mock_bus._log_audit = MagicMock()
    runner.command_bus = mock_bus

    mock_mds = AsyncMock()
    mock_mds.get_live_ticker.return_value = {"symbol": "BTC/USDT", "price": 51000.0}

    with patch.object(runner, "_ensure_market_data_service", return_value=mock_mds), \
         patch("services.autonomous_runner.telegram_service.notify_trade_close", new_callable=AsyncMock) as mock_tg:
        
        # Max positions reached to stop after open positions check
        runner.risk_engine.max_open_positions = 1
        result = await runner.step_cycle()

        assert "1R Kısmi Kâr Alındı" in runner.last_action
        assert "+$50.00" in runner.last_action
        assert "$50000.00" in runner.last_action
        assert pos.stop_loss == 50000.0
        mock_tg.assert_called_once()
