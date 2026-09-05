import pytest
from httpx import AsyncClient, ASGITransport
from apps.api.app.main import app
from shared.config import get_settings
from apps.api.app.api.state import RUNTIME_STATE


@pytest.mark.asyncio
async def test_emergency_shutdown_halts_system_and_cancels_orders():
    settings = get_settings()
    headers = {"X-API-KEY": getattr(settings, "API_ADMIN_KEY", "dev-insecure-key-change-in-production")}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # Trigger emergency shutdown
        resp = await ac.post("/api/system/emergency-shutdown", headers=headers)
        assert resp.status_code == 200
        data = resp.json()

        assert data["success"] is True
        assert data["status"] == "HALTED"
        assert data["system_state"] == "RISK_LOCK"
        assert data["is_halted"] is True
        assert "cancelled_orders_count" in data
        assert "open_positions_count" in data

        # Verify global runtime state
        assert RUNTIME_STATE["is_halted"] is True
        assert RUNTIME_STATE["system_state"] == "RISK_LOCK"
        assert RUNTIME_STATE["circuit_state"] == "EMERGENCY"

        # Verify that start agent fails while halted
        start_resp = await ac.post("/api/agent/start", headers=headers)
        assert start_resp.status_code == 200
        assert start_resp.json().get("success") is False
        assert "halted" in start_resp.json().get("message", "").lower()
