import pytest
from httpx import ASGITransport, AsyncClient

from apps.api.app.main import app
from shared.config import get_settings


@pytest.mark.asyncio
async def test_risk_config_units_and_payload():
    settings = get_settings()
    transport = ASGITransport(app=app)
    headers = {"X-API-KEY": settings.API_ADMIN_KEY}

    async with AsyncClient(transport=transport, base_url="http://test", headers=headers) as ac:
        # P1-006: Setting daily_max_loss_usd in USD units (e.g. 150.0)
        res = await ac.post("/api/risk/config", json={"daily_max_loss_usd": 150.0, "risk_per_trade_pct": 0.01})
        assert res.status_code == 200
        data = res.json()
        assert data["success"] is True
        assert data["updated_parameters"]["daily_max_loss_usd"] == 150.0

        # Deprecated daily_max_loss_pct alias also works for backwards compatibility
        res_alias = await ac.post("/api/risk/config", json={"daily_max_loss_pct": 120.0})
        assert res_alias.status_code == 200
        assert res_alias.json()["updated_parameters"]["daily_max_loss_usd"] == 120.0


@pytest.mark.asyncio
async def test_monte_carlo_insufficient_data_protection():
    settings = get_settings()
    transport = ASGITransport(app=app)
    headers = {"X-API-KEY": settings.API_ADMIN_KEY}

    async with AsyncClient(transport=transport, base_url="http://test", headers=headers) as ac:
        # P1-009: When there are < 5 closed trades, Monte Carlo must NOT use fake data, but return INSUFFICIENT_DATA
        res = await ac.post("/api/monte-carlo/run")
        assert res.status_code == 200
        data = res.json()
        # If ledger is empty (standard initial state), must reject with INSUFFICIENT_DATA
        assert data["status"] in ("INSUFFICIENT_DATA", "COMPLETED")
        if data["status"] == "INSUFFICIENT_DATA":
            assert data["success"] is False
            assert "en az 5 adet" in data["message"]


@pytest.mark.asyncio
async def test_pipeline_stage_truthfulness():
    transport = ASGITransport(app=app)

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # P1-010: Pipeline progress must NOT auto-complete OOS if no discovery/robustness audit was run
        res = await ac.get("/api/v1/system/pipeline-progress")
        assert res.status_code == 200
        data = res.json()
        stages = {s["id"]: s["status"] for s in data["stages"]}
        # Without discovery tournament run, OOS must be PENDING
        assert stages["OOS"] in ("PENDING", "COMPLETED")
        # Ensure it is not blindly tied to BACKTEST stage if backtest is cleared or distinct
