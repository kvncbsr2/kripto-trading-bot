import pytest
from fastapi.testclient import TestClient

from apps.api.app.main import app
from apps.api.app.api.state import RUNTIME_STATE
from services.config_manager.risk_profiles import (
    RISK_PROFILES,
    get_all_profiles,
    get_profile,
    apply_profile_to_system,
)
from shared.config import get_settings

settings = get_settings()


def test_all_10_profiles_exist_and_monotonic():
    """All 10 risk profiles must exist, be contiguous 1..10, and scale risk predictably."""
    assert len(RISK_PROFILES) == 10
    profiles = get_all_profiles()
    assert len(profiles) == 10

    for i in range(1, 11):
        p = get_profile(i)
        assert p.level == i
        assert 0.005 <= p.risk_per_trade <= 0.03
        assert 35.0 <= p.min_signal_score <= 70.0
        assert 1 <= p.max_open_positions <= 10

    # Monotonic risk progression: Level 1 has lowest risk, Level 10 has highest risk
    assert get_profile(1).risk_per_trade < get_profile(10).risk_per_trade
    # Opportunity / signal threshold drops as risk increases (accepts more trade opportunities)
    assert get_profile(1).min_signal_score > get_profile(10).min_signal_score
    assert get_profile(1).max_open_positions <= get_profile(10).max_open_positions


def test_level_1_is_exact_institutional_baseline():
    """Level 1 must represent the exact audited institutional baseline."""
    l1 = get_profile(1)
    assert l1.risk_per_trade == 0.005  # 0.5%
    assert l1.min_signal_score == 65.0
    assert l1.min_opportunity_score == 50.0
    assert l1.pivot_left == 5
    assert l1.pivot_right == 2
    assert l1.daily_target == 50.0
    assert l1.daily_max_loss == 50.0
    assert l1.max_open_positions == 2


def test_apply_profile_to_system_updates_runtime_and_engines():
    """Applying a profile must hot-reload engines in-memory without restarting."""
    res = apply_profile_to_system(5)
    assert res["success"] is True
    assert res["active_level"] == 5
    assert RUNTIME_STATE["active_profile_level"] == 5

    p5 = get_profile(5)
    assert RUNTIME_STATE["daily_target"] == p5.daily_target
    assert RUNTIME_STATE["daily_max_loss"] == p5.daily_max_loss

    # Cleanup back to Level 1
    apply_profile_to_system(1)
    assert RUNTIME_STATE["active_profile_level"] == 1


def test_rollback_to_level_1():
    """Testing immediate guaranteed rollback to Level 1."""
    # Scale up to high risk (Level 8)
    apply_profile_to_system(8)
    assert RUNTIME_STATE["active_profile_level"] == 8

    # Rollback to baseline
    res = apply_profile_to_system(1)
    assert res["success"] is True
    assert res["active_level"] == 1
    assert RUNTIME_STATE["active_profile_level"] == 1
    assert RUNTIME_STATE["daily_target"] == 50.0
    assert RUNTIME_STATE["daily_max_loss"] == 50.0


def test_invalid_level_boundary_handling():
    """Invalid level numbers must safely fall back to Level 1 without throwing."""
    res_low = apply_profile_to_system(0)
    assert res_low["active_level"] == 1

    res_high = apply_profile_to_system(99)
    assert res_high["active_level"] == 1


from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_api_profile_endpoints():
    """Verify REST API endpoints for risk profiles with authentication."""
    transport = ASGITransport(app=app)
    headers = {"X-API-KEY": settings.API_ADMIN_KEY}

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. GET /api/v1/system/profiles
        r = await ac.get("/api/v1/system/profiles")
        assert r.status_code == 200
        data = r.json()
        assert data["success"] is True
        assert len(data["profiles"]) == 10

        # 2. GET /api/v1/system/profile/current
        r = await ac.get("/api/v1/system/profile/current")
        assert r.status_code == 200
        cur_data = r.json()
        assert cur_data["success"] is True
        assert "profile" in cur_data

        # 3. POST /api/v1/system/profile without auth should fail 401
        r = await ac.post("/api/v1/system/profile", json={"level": 4})
        assert r.status_code == 401

        # 4. POST /api/v1/system/profile with auth succeeds
        r = await ac.post("/api/v1/system/profile", json={"level": 4}, headers=headers)
        assert r.status_code == 200
        assert r.json()["success"] is True
        assert r.json()["active_level"] == 4

        # 5. POST with invalid level (> 10) returns 422 Unprocessable Entity
        r = await ac.post("/api/v1/system/profile", json={"level": 15}, headers=headers)
        assert r.status_code == 422

        # 6. Reset back to Level 1
        r = await ac.post("/api/v1/system/profile", json={"level": 1}, headers=headers)
        assert r.status_code == 200
        assert r.json()["active_level"] == 1

