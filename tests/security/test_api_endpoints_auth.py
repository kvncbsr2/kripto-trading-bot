import pytest
from httpx import ASGITransport, AsyncClient

from apps.api.app.main import app
from shared.config import get_settings


@pytest.mark.asyncio
async def test_mutating_endpoints_require_auth():
    settings = get_settings()
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # All mutating operational endpoints must return 401 Unauthorized without auth
        mutating_requests = [
            ("POST", "/api/agent/start", {}),
            ("POST", "/api/agent/stop", {}),
            ("POST", "/api/agent/resume", {}),
            ("POST", "/system/reset", {"confirmation": True}),
            ("POST", "/system/emergency-shutdown", {}),
            ("POST", "/api/risk/config", {"risk_per_trade_pct": 0.01}),
            ("POST", "/risk/limits", {"risk_per_trade": 0.01}),
            ("POST", "/orders", {"symbol": "BTC/USDT", "side": "BUY", "order_type": "MARKET", "quantity": 0.01}),
            ("DELETE", "/orders/fake_order_id_123", {}),
            ("POST", "/positions/BTCUSDT/close", {}),
            ("POST", "/api/v1/trades/simulate", {"symbol": "BTC/USDT", "side": "BUY", "amount_usd": 100.0}),
        ]

        for method, endpoint, payload in mutating_requests:
            if method == "POST":
                res = await ac.post(endpoint, json=payload if payload else None)
            elif method == "DELETE":
                res = await ac.delete(endpoint)
            else:
                res = await ac.request(method, endpoint, json=payload)
            assert res.status_code == 401, f"Expected 401 Unauthorized for {method} {endpoint}, got {res.status_code}"

        # Test with invalid X-API-KEY
        res_bad_key = await ac.post("/system/emergency-shutdown", headers={"X-API-KEY": "wrong_key"})
        assert res_bad_key.status_code == 401

        # Test with invalid Bearer token
        res_bad_bearer = await ac.post("/system/emergency-shutdown", headers={"Authorization": "Bearer wrong_token"})
        assert res_bad_bearer.status_code == 401

        # Test with valid X-API-KEY header
        res_valid_key = await ac.post("/system/emergency-shutdown", headers={"X-API-KEY": settings.API_ADMIN_KEY})
        assert res_valid_key.status_code == 200
        assert res_valid_key.json()["status"] == "HALTED"

        # Test with valid Bearer token
        res_valid_bearer = await ac.post("/system/emergency-shutdown", headers={"Authorization": f"Bearer {settings.API_AUTH_SECRET}"})
        assert res_valid_bearer.status_code == 200
