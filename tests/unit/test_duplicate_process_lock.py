import json
import os
import pytest
from services.autonomous_runner import (
    _LOCK_FILE,
    AutonomousPaperTrader,
)
from services.command_bus.command_bus import CommandBus
from services.execution.paper_execution import PaperExecutionEngine
from services.market_data.market_data_service import MarketDataService
from services.risk_engine.risk_engine import RiskEngine


@pytest.fixture(autouse=True)
def clean_lock():
    if os.path.exists(_LOCK_FILE):
        try:
            os.remove(_LOCK_FILE)
        except OSError:
            pass
    yield
    if os.path.exists(_LOCK_FILE):
        try:
            os.remove(_LOCK_FILE)
        except OSError:
            pass


def test_lock_acquire_and_release():
    token = AutonomousPaperTrader._acquire_worker_lock()
    assert token is not None
    assert os.path.exists(_LOCK_FILE)

    with open(_LOCK_FILE, "r") as f:
        data = json.load(f)
    assert data["pid"] == os.getpid()
    assert data["owner_token"] == token

    # Second acquire without clearing should return existing or refreshed for current PID
    # But release with wrong token fails
    AutonomousPaperTrader._release_worker_lock("wrong-token")
    assert os.path.exists(_LOCK_FILE)

    # Release with correct token removes lock
    AutonomousPaperTrader._release_worker_lock(token)
    assert not os.path.exists(_LOCK_FILE)


def test_lock_collision_foreign_process():
    # Simulate an active foreign process PID
    stale_payload = {
        "pid": 99999999,
        "owner_token": "foreign-uuid-1234",
        "timestamp": 123456789.0,
    }
    with open(_LOCK_FILE, "w") as f:
        json.dump(stale_payload, f)

    # Should detect stale dead process and acquire successfully
    new_token = AutonomousPaperTrader._acquire_worker_lock()
    assert new_token is not None
    with open(_LOCK_FILE, "r") as f:
        data = json.load(f)
    assert data["pid"] == os.getpid()
    assert data["owner_token"] == new_token

    AutonomousPaperTrader._release_worker_lock(new_token)


def test_duplicate_autonomous_runner_lock_collision(monkeypatch):
    # Mock _is_pid_alive to return True for a simulated foreign PID
    from services import autonomous_runner
    monkeypatch.setattr(autonomous_runner, "_is_pid_alive", lambda pid: True)

    # Write a foreign lock
    foreign_payload = {
        "pid": 54321,
        "owner_token": "foreign-active-token",
        "timestamp": 123456789.0,
    }
    with open(_LOCK_FILE, "w") as f:
        json.dump(foreign_payload, f)

    # Attempting to start AutonomousPaperTrader should raise RuntimeError
    broker = PaperExecutionEngine(initial_balance=1000.0)
    command_bus = CommandBus(runtime_state={"balance": 1000.0, "equity": 1000.0}, broker=broker)
    market_data = MarketDataService(symbols=["BTCUSDT"])
    risk_engine = RiskEngine()

    runner = AutonomousPaperTrader(command_bus, market_data, risk_engine)

    with pytest.raises(RuntimeError, match="Only one trading loop can run at a time"):
        runner.start()
