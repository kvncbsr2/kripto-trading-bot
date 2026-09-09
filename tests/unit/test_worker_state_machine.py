import asyncio
import os
import tempfile
import pytest
from httpx import ASGITransport, AsyncClient

from apps.api.app.api.state import RUNTIME_STATE, command_bus, risk_engine
from apps.api.app.main import app
from services.autonomous_runner import AutonomousPaperTrader, autonomous_trader
from services.execution.order_manager import OrderManager
from services.execution.paper_execution import PaperExecutionEngine
from shared.config import get_settings
from shared.enums import (
    PositionSide,
    PositionStatus,
    SignalDirection,
    TradingWorkerState,
)
from shared.schemas import RiskDecision


@pytest.fixture(autouse=True)
def clean_runner_state():
    autonomous_trader.reset_state()
    if command_bus.broker and hasattr(command_bus.broker, 'unhalt'):
        command_bus.broker.unhalt()
    RUNTIME_STATE['is_halted'] = False
    RUNTIME_STATE['system_state'] = 'READY'
    yield
    autonomous_trader.reset_state()
    if command_bus.broker and hasattr(command_bus.broker, 'unhalt'):
        command_bus.broker.unhalt()
    RUNTIME_STATE['is_halted'] = False


@pytest.mark.asyncio
async def test_only_one_trading_loop_can_run():
    runner1 = AutonomousPaperTrader()
    runner2 = AutonomousPaperTrader()

    runner1.start()
    assert runner1.worker_state == TradingWorkerState.RUNNING

    with pytest.raises(RuntimeError, match="Only one trading loop can run"):
        runner2.start()

    runner1.stop()
    assert runner1.worker_state == TradingWorkerState.STOPPED


@pytest.mark.asyncio
async def test_duplicate_worker_start_is_rejected():
    runner = AutonomousPaperTrader()
    runner.start()
    assert runner.worker_state == TradingWorkerState.RUNNING

    with pytest.raises(RuntimeError, match="Duplicate worker start is rejected"):
        runner.start()

    runner.stop()
    assert runner.worker_state == TradingWorkerState.STOPPED


@pytest.mark.asyncio
async def test_api_start_really_starts_worker():
    settings = get_settings()
    headers = {"X-API-KEY": getattr(settings, "API_ADMIN_KEY", "dev-insecure-key-change-in-production")}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/api/agent/start", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert autonomous_trader.worker_state == TradingWorkerState.RUNNING
        assert autonomous_trader.is_active is True
        assert RUNTIME_STATE["is_autonomous_active"] is True
        assert RUNTIME_STATE["system_state"] == "TRADING"

    autonomous_trader.stop()


@pytest.mark.asyncio
async def test_api_stop_really_stops_worker():
    settings = get_settings()
    headers = {"X-API-KEY": getattr(settings, "API_ADMIN_KEY", "dev-insecure-key-change-in-production")}

    # First start the trader
    autonomous_trader.start()
    assert autonomous_trader.worker_state == TradingWorkerState.RUNNING

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.post("/api/agent/stop", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["success"] is True
        assert data["status"] == "STOPPED"
        assert autonomous_trader.worker_state == TradingWorkerState.STOPPED
        assert autonomous_trader.is_active is False
        assert autonomous_trader._task is None
        assert RUNTIME_STATE["system_state"] == "STOPPED"


@pytest.mark.asyncio
async def test_emergency_stop_blocks_new_orders():
    broker = PaperExecutionEngine(initial_balance=5000.0)
    om = OrderManager(execution_engine=broker, risk_engine=risk_engine)

    runner = AutonomousPaperTrader()
    runner.start()
    assert runner.worker_state == TradingWorkerState.RUNNING

    # Trigger emergency stop
    runner.emergency_stop(reason="Test Emergency Stop")
    assert runner.worker_state == TradingWorkerState.EMERGENCY_STOP
    assert runner.is_active is False

    # Also halt broker directly as done in emergency stop
    broker.emergency_close_all()
    assert broker.is_halted is True

    # Starting worker while in EMERGENCY_STOP must be rejected
    with pytest.raises(RuntimeError, match="Emergency stop is active"):
        runner.start()

    # Attempting to execute order via broker while halted must raise PermissionError
    decision = RiskDecision(
        symbol="BTC/USDT",
        approved=True,
        direction=SignalDirection.LONG,
        entry_price=50000.0,
        calculated_size=0.01,
        stop_loss=49000.0,
        take_profit=52000.0,
        risk_amount_usd=10.0,
    )

    with pytest.raises(PermissionError, match="halted by emergency stop"):
        broker.execute_market_order(decision)

    # Attempting to execute via OrderManager while broker is halted must raise PermissionError
    with pytest.raises(PermissionError, match="HALTED by emergency stop"):
        await om.execute_risk_decision(decision, strategy_name="test")

    runner.reset_state()
    assert runner.worker_state == TradingWorkerState.STOPPED


@pytest.mark.asyncio
async def test_restart_recovers_correct_state():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    try:
        # 1. Instance 1 executes a trade and is stopped
        engine1 = PaperExecutionEngine(initial_balance=10000.0, db_path=db_path)
        decision = RiskDecision(
            symbol="ETH/USDT",
            approved=True,
            direction=SignalDirection.LONG,
            entry_price=3000.0,
            calculated_size=1.0,
            stop_loss=2900.0,
            take_profit=3200.0,
            risk_amount_usd=100.0,
        )
        engine1.execute_market_order(decision)
        engine1.close_position("ETH/USDT", exit_price=3100.0, exit_reason="TAKE_PROFIT")

        saved_balance = engine1.balance
        saved_pnl = engine1.total_realized_pnl
        assert len(engine1.closed_positions_history) == 1

        # 2. Instance 2 recovers from same DB
        engine2 = PaperExecutionEngine(initial_balance=10000.0, db_path=db_path)
        assert round(engine2.balance, 2) == round(saved_balance, 2)
        assert round(engine2.total_realized_pnl, 2) == round(saved_pnl, 2)
        assert len(engine2.closed_positions_history) == 1

        # 3. Verify clean runner reset recovering to STOPPED state
        runner = AutonomousPaperTrader()
        runner.emergency_stop()
        assert runner.worker_state == TradingWorkerState.EMERGENCY_STOP

        runner.reset_state()
        assert runner.worker_state == TradingWorkerState.STOPPED
        assert runner.is_active is False
        # Clean start is now possible
        runner.start()
        assert runner.worker_state == TradingWorkerState.RUNNING
        runner.stop()
    finally:
        if os.path.exists(db_path):
            os.unlink(db_path)
