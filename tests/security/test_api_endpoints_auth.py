import pytest
from httpx import ASGITransport, AsyncClient
from apps.api.app.main import app
from shared.config import get_settings


@pytest.mark.asyncio
async def test_mutating_endpoints_require_auth_when_enabled():
    settings = get_settings()
    original_auth_state = settings.API_KEY_AUTH_ENABLED

    try:
        # Enable authentication requirement
        settings.API_KEY_AUTH_ENABLED = True
        transport = ASGITransport(app=app)

        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # 1. Calling /api/v1/trades/simulate without auth must return 401
            res1 = await ac.post("/api/v1/trades/simulate", json={"symbol": "BTC/USDT", "side": "BUY", "amount_usd": 100.0})
            assert res1.status_code == 401, f"Expected 401 Unauthorized but got {res1.status_code}"

            # 2. Calling /risk/limits without auth must return 401
            res2 = await ac.post("/risk/limits", json={"risk_per_trade": 0.01})
            assert res2.status_code == 401

            # 3. Calling /system/emergency-shutdown without auth must return 401
            res3 = await ac.post("/system/emergency-shutdown")
            assert res3.status_code == 401

            # 4. Calling with valid X-API-KEY must succeed
            headers = {"X-API-KEY": settings.API_ADMIN_KEY}
            res4 = await ac.post("/system/emergency-shutdown", headers=headers)
            assert res4.status_code == 200
            assert res4.json()["status"] == "HALTED"

    finally:
        settings.API_KEY_AUTH_ENABLED = original_auth_state
