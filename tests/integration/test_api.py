import pytest
from httpx import ASGITransport, AsyncClient

from apps.api.app.main import app
from shared.config import get_settings

settings = get_settings()


@pytest.mark.asyncio
async def test_health_endpoints():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["live_trading"] is False
        assert data["paper_trading"] is True

        resp_ready = await ac.get("/health/ready")
        assert resp_ready.status_code == 200


@pytest.mark.asyncio
async def test_market_and_portfolio_endpoints():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp_mkt = await ac.get("/market/status")
        assert resp_mkt.status_code == 200
        assert "BTC/USDT" in resp_mkt.json()["supported_symbols"]

        resp_port = await ac.get("/portfolio")
        assert resp_port.status_code == 200
        assert resp_port.json()["initial_capital"] == 5000.0

        resp_risk = await ac.get("/risk/status")
        assert resp_risk.status_code == 200
        from apps.api.app.api.state import risk_engine
        assert resp_risk.json()["daily_max_loss_usd"] == risk_engine.daily_max_loss_usd

        resp_sys = await ac.get("/system/status")
        assert resp_sys.status_code == 200
        assert resp_sys.json()["live_trading_prohibited"] is True


@pytest.mark.asyncio
async def test_dashboard_page():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/dashboard")
        assert resp.status_code == 200
        assert "KRIPTO AGENT" in resp.text
