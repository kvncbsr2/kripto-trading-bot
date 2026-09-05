import pytest
from httpx import ASGITransport, AsyncClient

from apps.api.app.main import app
from shared.config import get_settings

settings = get_settings()


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.mark.asyncio
async def test_readiness_gate_endpoint(client: AsyncClient):
    response = await client.get("/api/system/readiness")
    assert response.status_code == 200
    data = response.json()
    assert "ready" in data
    assert "checks" in data
    assert data["checks"]["safety_lock"] is True


@pytest.mark.asyncio
async def test_agent_lifecycle_controls(client: AsyncClient):
    # 1. Start Agent
    res = await client.post("/api/agent/start")
    assert res.status_code == 200
    assert res.json()["success"] is True
    assert res.json()["system_state"] == "TRADING"

    # 2. Pause Agent
    res = await client.post("/api/agent/pause")
    assert res.status_code == 200
    assert res.json()["system_state"] == "PAUSED"

    # 3. Resume Agent
    res = await client.post("/api/agent/resume")
    assert res.status_code == 200
    assert res.json()["system_state"] == "TRADING"

    # 4. Stop Agent
    res = await client.post("/api/agent/stop")
    assert res.status_code == 200
    assert res.json()["system_state"] == "STOPPED"


@pytest.mark.asyncio
async def test_emergency_stop_command(client: AsyncClient):
    res = await client.post("/api/agent/emergency-stop")
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["system_state"] == "RISK_LOCK"
    assert data["is_halted"] is True

    # Resume must be blocked during RISK_LOCK
    resume_res = await client.post("/api/agent/resume")
    assert resume_res.json()["success"] is False


@pytest.mark.asyncio
async def test_strategy_toggle_command(client: AsyncClient):
    # Disable Trend Following
    res = await client.post(
        "/api/strategies/trend_following/toggle",
        json={"enabled": False},
    )
    assert res.status_code == 200
    assert res.json()["enabled"] is False

    # Re-enable Trend Following
    res = await client.post(
        "/api/strategies/trend_following/toggle",
        json={"enabled": True},
    )
    assert res.status_code == 200
    assert res.json()["enabled"] is True


@pytest.mark.asyncio
async def test_risk_config_update(client: AsyncClient):
    res = await client.post(
        "/api/risk/config",
        json={
            "risk_per_trade_pct": 0.007,
            "daily_max_loss_pct": 60.0,
            "max_open_positions": 3,
            "atr_multiplier": 1.8,
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["updated_parameters"]["risk_per_trade_pct"] == 0.007
    assert data["updated_parameters"]["max_open_positions"] == 3


@pytest.mark.asyncio
async def test_scanner_run_action(client: AsyncClient):
    res = await client.post("/api/scanner/run")
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["scanned_count"] > 0
    assert len(data["opportunities"]) > 0


@pytest.mark.asyncio
async def test_vectorbt_backtest_endpoint(client: AsyncClient):
    from unittest.mock import AsyncMock, patch
    from datetime import datetime, timezone, timedelta
    from shared.schemas import Candle
    from shared.enums import Timeframe

    base_time = datetime.now(timezone.utc)
    mock_candles = [
        Candle(
            symbol="BTC/USDT",
            timeframe=Timeframe.M15,
            timestamp=base_time + timedelta(minutes=15 * i),
            open=60000.0 + i * 10,
            high=60020.0 + i * 10,
            low=59980.0 + i * 10,
            close=60010.0 + i * 10,
            volume=100.0,
        )
        for i in range(100)
    ]
    with patch(
        "apps.api.app.api.routers.backtests.market_data_service.get_historical_klines",
        new=AsyncMock(return_value=mock_candles),
    ):
        res = await client.post(
            "/api/backtest/run",
            json={
                "symbol": "BTC/USDT",
                "timeframe": "15m",
                "strategy": "trend_following",
                "initial_capital": 5000.0,
                "fees": 0.001,
                "slippage_bps": 5.0,
            },
        )
        assert res.status_code == 200
        data = res.json()
        assert data["engine"] == "vectorbt"
        assert "total_net_pnl" in data
        assert "win_rate" in data


@pytest.mark.asyncio
async def test_monte_carlo_endpoint(client: AsyncClient):
    res = await client.post("/api/monte-carlo/run")
    assert res.status_code == 200
    data = res.json()
    assert data["success"] is True
    assert data["simulations"] == 1000
    assert "worst_case_drawdown" in data["metrics"]


@pytest.mark.asyncio
async def test_audit_logs_endpoint(client: AsyncClient):
    res = await client.get("/api/system/audit-logs")
    assert res.status_code == 200
    logs = res.json()
    assert isinstance(logs, list)
    # Since previous tests executed actions, audit logs must not be empty
    assert len(logs) > 0
    assert any(log["action"] == "EMERGENCY_STOP" for log in logs)


@pytest.mark.asyncio
async def test_ai_agents_endpoint(client: AsyncClient):
    res = await client.get("/api/ai/agents")
    assert res.status_code == 200
    data = res.json()
    assert "technical_agent" in data
    assert "risk_agent" in data
    assert "orchestrator" in data
    assert data["technical_agent"]["status"] == "ONLINE"
