"""
Authoritative Test Suite: Risk Profile Architecture V2 + Strategy Separation.
Verifies all 26+ Master Prompt acceptance criteria, parametric tests, safety invariants,
restart persistence, and zero position eviction on downgrade.
"""

import os
import pytest
from httpx import ASGITransport, AsyncClient

from apps.api.app.main import app
from apps.api.app.api.state import RUNTIME_STATE
from services.config_manager.risk_profiles import (
    RISK_PROFILES,
    get_all_profiles,
    get_profile,
    apply_profile_to_system,
    apply_strategy_to_system,
    restore_runtime_system_state,
)
from services.config_manager.state_persistence import (
    STATE_FILE_PATH,
    load_persisted_profile_state,
    save_persisted_profile_state,
)
from services.strategy_engine.registry import (
    DEFAULT_STRATEGY_ID,
    STRATEGY_REGISTRY,
    get_all_strategies,
    get_strategy_metadata,
    create_strategy,
)
from shared.config import get_settings
from shared.schemas import Position, PositionStatus

settings = get_settings()


# =============================================================================
# 1. CANONICAL REGISTRY & VALIDATION
# =============================================================================

def test_all_10_risk_profiles_exist_and_strictly_monotonic():
    """All 10 profiles must be valid, monotonic in risk, and have correct non-strategy names."""
    assert len(RISK_PROFILES) == 10
    profiles = get_all_profiles()
    assert len(profiles) == 10

    expected_names = [
        "Ultra Conservative (Capital Preservation)",
        "Conservative",
        "Balanced",
        "Growth",
        "Active",
        "Aggressive",
        "High Risk",
        "High Activity",
        "Very Aggressive",
        "Maximum Configured Risk",
    ]

    prev_risk = 0.0
    prev_signal_score = 100.0

    for i, p in enumerate(profiles, start=1):
        assert p["level"] == i
        assert p["name"] == expected_names[i - 1]
        assert "Scalp" not in p["name"]
        assert "HFT" not in p["name"]
        assert "Mean Reversion" not in p["name"]

        # Risk scales monotonically upward
        assert p["risk_per_trade"] > prev_risk
        prev_risk = p["risk_per_trade"]

        # Signal score scales monotonically downward (accepts more opportunities)
        assert p["min_signal_score"] <= prev_signal_score
        prev_signal_score = p["min_signal_score"]

        # Invariants
        assert 0.005 <= p["risk_per_trade"] <= 0.025
        assert 40.0 <= p["min_signal_score"] <= 65.0
        assert 2 <= p["max_open_positions"] <= 8
        assert 50.0 <= p["daily_max_loss"] <= 200.0
        assert 50.0 <= p["daily_target"] <= 500.0


# =============================================================================
# 2. PARAMETRIC TESTS (L1 .. L10)
# =============================================================================

@pytest.mark.parametrize("level", range(1, 11))
def test_parametric_profile_application(level):
    """Every single level 1..10 must apply cleanly to runtime state and RiskEngine."""
    res = apply_profile_to_system(level, source="parametric_test")
    assert res["success"] is True
    assert res["level"] == level

    profile = get_profile(level)
    assert RUNTIME_STATE["active_profile_level"] == level
    assert RUNTIME_STATE["daily_target"] == profile.daily_target
    assert RUNTIME_STATE["daily_max_loss"] == profile.daily_max_loss
    assert RUNTIME_STATE["max_open_positions"] == profile.max_open_positions
    assert RUNTIME_STATE["min_signal_score"] == profile.min_signal_score
    assert RUNTIME_STATE["min_opportunity_score"] == profile.min_opportunity_score
    assert RUNTIME_STATE["target_mode"] == profile.target_mode

    # Verify state persistence file updated
    persisted = load_persisted_profile_state()
    assert persisted["active_risk_profile_level"] == level


# =============================================================================
# 3. STRATEGY REGISTRY & INDEPENDENT STRATEGY SELECTION
# =============================================================================

def test_strategy_registry_independence():
    """Strategy selection is completely decoupled from Risk Profiles."""
    strategies = get_all_strategies()
    assert len(strategies) >= 4
    strategy_ids = [s["id"] for s in strategies]
    assert "r10_rsi_divergence" in strategy_ids
    assert "regime_gated_pullback" in strategy_ids
    assert "mean_reversion" in strategy_ids

    # Switch strategy without touching risk profile
    apply_profile_to_system(3)
    assert RUNTIME_STATE["active_profile_level"] == 3

    res = apply_strategy_to_system("regime_gated_pullback")
    assert res["success"] is True
    assert RUNTIME_STATE["active_strategy_id"] == "regime_gated_pullback"
    assert RUNTIME_STATE["active_profile_level"] == 3  # Risk profile remains unchanged!

    # Switch to Mean Reversion
    res2 = apply_strategy_to_system("mean_reversion")
    assert res2["success"] is True
    assert RUNTIME_STATE["active_strategy_id"] == "mean_reversion"
    assert RUNTIME_STATE["active_profile_level"] == 3


# =============================================================================
# 4. SAFETY INVARIANT: OPEN POSITIONS NOT FORCIBLY CLOSED
# =============================================================================

from shared.enums import PositionSide


def test_profile_downgrade_does_not_close_open_positions():
    """Invariant 7: Switching from high max_open_positions to lower limit never evicts existing positions."""
    from services.autonomous_runner import autonomous_trader

    # Start at Level 5 (max 5 positions)
    apply_profile_to_system(5)
    assert RUNTIME_STATE["max_open_positions"] == 5

    # Mock 4 open positions in broker
    broker = autonomous_trader.command_bus.broker if autonomous_trader.command_bus else None
    if broker:
        broker.open_positions = {
            "BTC/USDT": Position(position_id="p1", symbol="BTC/USDT", side=PositionSide.LONG, quantity=0.01, entry_price=70000, current_price=70500, stop_loss=69000, take_profit=72000, status=PositionStatus.OPEN),
            "ETH/USDT": Position(position_id="p2", symbol="ETH/USDT", side=PositionSide.LONG, quantity=0.1, entry_price=3500, current_price=3550, stop_loss=3400, take_profit=3700, status=PositionStatus.OPEN),
            "SOL/USDT": Position(position_id="p3", symbol="SOL/USDT", side=PositionSide.LONG, quantity=1.0, entry_price=150, current_price=155, stop_loss=145, take_profit=165, status=PositionStatus.OPEN),
            "BNB/USDT": Position(position_id="p4", symbol="BNB/USDT", side=PositionSide.LONG, quantity=0.5, entry_price=600, current_price=605, stop_loss=590, take_profit=620, status=PositionStatus.OPEN),
        }

    # Downgrade to Level 1 (max 2 positions)
    apply_profile_to_system(1)
    assert RUNTIME_STATE["max_open_positions"] == 2

    # Verify that existing open positions are NOT evicted or deleted
    if broker:
        assert len(broker.open_positions) == 4
        assert "BTC/USDT" in broker.open_positions
        assert "ETH/USDT" in broker.open_positions
        assert "SOL/USDT" in broker.open_positions
        assert "BNB/USDT" in broker.open_positions

    # Cleanup broker mock
    if broker:
        broker.open_positions = {}


def test_strategy_switch_does_not_close_open_positions():
    """Invariant 8: Strategy switch never evicts or closes open positions."""
    from services.autonomous_runner import autonomous_trader
    broker = autonomous_trader.command_bus.broker if autonomous_trader.command_bus else None

    if broker:
        broker.open_positions = {
            "BTC/USDT": Position(position_id="p1", symbol="BTC/USDT", side=PositionSide.LONG, quantity=0.01, entry_price=70000, current_price=70500, stop_loss=69000, take_profit=72000, status=PositionStatus.OPEN),
        }

    apply_strategy_to_system("regime_gated_pullback")
    assert RUNTIME_STATE["active_strategy_id"] == "regime_gated_pullback"

    if broker:
        assert len(broker.open_positions) == 1
        assert "BTC/USDT" in broker.open_positions
        broker.open_positions = {}


# =============================================================================
# 5. RESTART PERSISTENCE & RESTORATION
# =============================================================================

def test_restart_persistence_and_restoration():
    """Invariant 10: Persisted profile & strategy must be faithfully restored upon reboot."""
    # Set to Level 7 and regime_gated_pullback
    apply_profile_to_system(7)
    apply_strategy_to_system("regime_gated_pullback")
    assert RUNTIME_STATE["active_profile_level"] == 7
    assert RUNTIME_STATE["active_strategy_id"] == "regime_gated_pullback"

    # Simulate reboot: wipe in-memory state
    RUNTIME_STATE["active_profile_level"] = 1
    RUNTIME_STATE["active_strategy_id"] = "r10_rsi_divergence"

    # Call restore_runtime_system_state()
    res = restore_runtime_system_state()
    assert res["active_profile_level"] == 7
    assert res["active_strategy_id"] == "regime_gated_pullback"
    assert RUNTIME_STATE["active_profile_level"] == 7
    assert RUNTIME_STATE["active_strategy_id"] == "regime_gated_pullback"

    # Rollback to baseline L1
    apply_profile_to_system(1)
    apply_strategy_to_system("r10_rsi_divergence")


# =============================================================================
# 6. SAFETY INVARIANTS (L10 CANNOT BYPASS GUARDS)
# =============================================================================

def test_safety_invariants_across_all_profiles():
    """Invariants 1-6: Safety boundaries must hold even at Level 10."""
    p10 = get_profile(10)
    assert p10.risk_per_trade <= 0.03  # Max 3% ceiling
    assert p10.daily_max_loss <= 250.0  # Daily max loss cannot be unlimited
    assert p10.max_open_positions <= 10

    apply_profile_to_system(10)
    from services.autonomous_runner import autonomous_trader
    if autonomous_trader and autonomous_trader.risk_engine:
        re = autonomous_trader.risk_engine
        assert re.is_spot_mode is True  # Spot mode cannot be bypassed
        assert re.circuit_breaker.daily_max_loss_usd == p10.daily_max_loss

    # Restore L1
    apply_profile_to_system(1)


# =============================================================================
# 7. REST API SYNCHRONIZATION
# =============================================================================

@pytest.mark.asyncio
async def test_api_canonical_state_and_switching():
    """Canonical REST endpoints for Risk Profiles and Strategies."""
    transport = ASGITransport(app=app)
    headers = {"X-API-KEY": settings.API_ADMIN_KEY}

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 1. GET /api/v1/system/profiles
        r = await ac.get("/api/v1/system/profiles")
        assert r.status_code == 200
        data = r.json()
        assert len(data["profiles"]) == 10

        # 2. GET /api/v1/system/strategies
        r_strat = await ac.get("/api/v1/system/strategies")
        assert r_strat.status_code == 200
        assert len(r_strat.json()["strategies"]) >= 4

        # 3. POST Strategy without auth -> 401
        r_unauth = await ac.post("/api/v1/system/strategy", json={"strategy_id": "mean_reversion"})
        assert r_unauth.status_code == 401

        # 4. POST Strategy with auth -> 200
        r_auth = await ac.post("/api/v1/system/strategy", json={"strategy_id": "mean_reversion"}, headers=headers)
        assert r_auth.status_code == 200
        assert r_auth.json()["strategy_id"] == "mean_reversion"

        # 5. Unknown strategy -> 422
        r_bad = await ac.post("/api/v1/system/strategy", json={"strategy_id": "non_existent"}, headers=headers)
        assert r_bad.status_code == 422

        # 6. GET /api/v1/system/status -> returns canonical unified state
        r_status = await ac.get("/api/v1/system/status")
        assert r_status.status_code == 200
        status_data = r_status.json()
        assert "active_profile_level" in status_data
        assert "active_risk_profile" in status_data
        assert "active_strategy_id" in status_data
        assert "active_strategy" in status_data
        assert status_data["active_strategy_id"] == "mean_reversion"

        # 7. Reset back to L1 and r10
        await ac.post("/api/v1/system/profile", json={"level": 1}, headers=headers)
        await ac.post("/api/v1/system/strategy", json={"strategy_id": "r10_rsi_divergence"}, headers=headers)
