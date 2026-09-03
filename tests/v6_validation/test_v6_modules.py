from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest
from httpx import ASGITransport, AsyncClient

from apps.api.app.main import app
from services.backtest_engine.anti_lookahead import AntiLookaheadEngine
from services.strategy_discovery.discovery_engine import StrategyDiscoveryEngine
from services.strategy_discovery.overfitting_engine import OverfittingProtectionEngine
from services.strategy_engine.strategies.r10_rsi_divergence import R10RSIDivergenceStrategy
from shared.enums import SignalDirection


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# =====================================================================
# 1. R10 RSI DIVERGENCE & CAUSAL PIVOT TESTS (Sections 6-15, 84-86)
# =====================================================================


def test_r10_pivot_causality_and_no_lookahead():
    """
    Asserts that at index T, a pivot is UNCONFIRMED.
    The pivot can ONLY be confirmed after right_bars (5) have elapsed (at T + 5).
    """
    strategy = R10RSIDivergenceStrategy(left_bars=5, right_bars=5)

    dates = [datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(days=i) for i in range(25)]
    # Create a swing low valley at index 10:
    lows = [100.0] * 25
    lows[10] = 80.0  # Pivot low
    highs = [105.0] * 25
    closes = [102.0] * 25
    rsis = [50.0] * 25
    rsis[10] = 25.0

    df = pd.DataFrame(
        {
            "timestamp": dates,
            "low": lows,
            "high": highs,
            "close": closes,
            "rsi": rsis,
        }
    )

    # At bar 10 (when the low happens), the pivot MUST NOT be confirmed
    low_pivots_at_10, _ = strategy.detect_pivots_strictly_causal(df, current_idx=10)
    assert len(low_pivots_at_10) == 0

    # At bar 14 (T+4), still unconfirmed
    low_pivots_at_14, _ = strategy.detect_pivots_strictly_causal(df, current_idx=14)
    assert len(low_pivots_at_14) == 0

    # At bar 15 (T+5, right_bars elapsed), pivot is officially confirmed!
    low_pivots_at_15, _ = strategy.detect_pivots_strictly_causal(df, current_idx=15)
    assert len(low_pivots_at_15) == 1
    p = low_pivots_at_15[0]
    assert p.index == 10
    assert p.price == 80.0
    assert p.confirmation_index == 15
    assert p.timestamp != p.confirmation_time


def test_r10_bullish_divergence_signal():
    """
    Tests detection of regular bullish divergence (Price Lower Low, RSI Higher Low).
    """
    strategy = R10RSIDivergenceStrategy(left_bars=5, right_bars=5, min_signal_score=60.0)

    dates = [datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(days=i) for i in range(40)]
    lows = [100.0] * 40
    highs = [105.0] * 40
    closes = [102.0] * 40
    rsis = [50.0] * 40

    # Pivot 1 at index 10: Price=85, RSI=24
    lows[10] = 85.0
    rsis[10] = 24.0

    # Pivot 2 at index 25: Price=80 (Lower Low), RSI=32 (Higher Low)
    lows[25] = 80.0
    rsis[25] = 32.0

    df = pd.DataFrame(
        {
            "timestamp": dates,
            "low": lows,
            "high": highs,
            "close": closes,
            "rsi": rsis,
            "atr": [2.5] * 40,
        }
    )

    # Evaluated at index 30 (confirmation of Pivot 2 at 25 + 5)
    df_eval = df.iloc[:31].copy()
    signal = strategy.evaluate_from_dataframe(df_eval, symbol="BTC/USDT")

    assert signal is not None
    assert signal.direction == SignalDirection.LONG
    assert signal.strategy == "R10_RSI_DIVERGENCE"
    assert signal.metadata["divergence_type"] == "REGULAR_BULLISH"
    assert signal.metadata["is_real_time_safe"] is True


# =====================================================================
# 2. ANTI-LOOKAHEAD ENGINE AUDIT TESTS (Section 29)
# =====================================================================


def test_anti_lookahead_audit_clean_pass():
    audit_engine = AntiLookaheadEngine()
    t0 = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)
    t1 = t0 + timedelta(hours=1)
    t2 = t1 + timedelta(seconds=1)

    # Valid causal decision sequence
    audit_engine.record_decision_event(
        event_id="evt_01",
        bar_index=10,
        data_available_until=t0,
        decision_timestamp=t1,
        execution_timestamp=t2,
        pivot_timestamp=t0 - timedelta(hours=5),
    )

    result = audit_engine.run_audit()
    assert result.is_valid is True
    assert result.lookahead_violations_count == 0


def test_anti_lookahead_audit_catches_violations():
    audit_engine = AntiLookaheadEngine()
    t0 = datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc)

    # Violation: decision timestamp is before data availability timestamp
    audit_engine.record_decision_event(
        event_id="evt_future_data",
        bar_index=15,
        data_available_until=t0 + timedelta(hours=2),
        decision_timestamp=t0,
        execution_timestamp=t0 + timedelta(hours=1),
    )

    result = audit_engine.run_audit()
    assert result.is_valid is False
    assert result.lookahead_violations_count >= 1
    assert result.violations[0]["rule"] == "FUTURE_DATA_LEAKAGE"


# =====================================================================
# 3. OVERFITTING & ROBUSTNESS ENGINE TESTS (Sections 23-28, 42-43)
# =====================================================================


def test_purged_time_series_partitioning():
    # 100 bars: 60 train, 5 embargo, 20 val, 5 embargo, 10 test
    (tr_s, tr_e), (va_s, va_e), (te_s, te_e) = (
        OverfittingProtectionEngine.partition_purged_time_series(100, embargo_bars=5)
    )
    assert tr_s == 0 and tr_e == 60
    assert va_s == 65  # 5 bars embargo
    assert te_s == 85  # 5 bars embargo


def test_parameter_plateau_stability():
    # Stable plateau with similar profit factors
    stable, score = OverfittingProtectionEngine.evaluate_parameter_plateau(
        base_param_value=14.0, neighborhood_pfs=[1.45, 1.48, 1.42, 1.44, 1.46]
    )
    assert stable is True
    assert score >= 70.0

    # Fragile spike (overfitted single point)
    unstable, fragile_score = OverfittingProtectionEngine.evaluate_parameter_plateau(
        base_param_value=14.0, neighborhood_pfs=[0.8, 2.8, 0.7, 0.9]
    )
    assert unstable is False
    assert fragile_score < 60.0


def test_robustness_score_and_promotion_decision():
    # Strategy with solid OOS, stable plateau, passed stress tests
    report = OverfittingProtectionEngine.calculate_robustness_score(
        strategy_slug="r10-rsi-v1",
        version="1.0",
        is_trades_count=25,
        oos_profit_factor=1.45,
        oos_expectancy=35.0,
        oos_max_drawdown_pct=8.5,
        walk_forward_stability=0.88,
        monte_carlo_drawdown_95th=11.2,
        plateau_score=80.0,
        fee_stress_passed=True,
        fee_score=85.0,
        slippage_stress_passed=True,
        slippage_score=90.0,
        coins_tested_count=8,
        coins_profitable_count=7,
        regimes_profitable_count=3,
        has_lookahead_violation=False,
    )

    assert report.robustness_score >= 70.0
    assert report.decision == "PROMOTED"
    assert report.fee_stress_passed is True
    assert report.slippage_stress_passed is True


# =====================================================================
# 4. STRATEGY DISCOVERY & TOURNAMENT TESTS (Sections 30-31, 44)
# =====================================================================


def test_strategy_discovery_variants_and_tournament():
    discovery = StrategyDiscoveryEngine()
    candidates = discovery.generate_r10_variants()

    assert len(candidates) == 10
    variants = [c.variant for c in candidates]
    assert "R10-V1" in variants
    assert "R10-V10" in variants

    # Run tournament with synthetic series
    np.random.seed(42)
    prices = 50000.0 * np.exp(np.cumsum(np.random.normal(0.001, 0.015, 100)))
    series = pd.Series(prices)

    ranked = discovery.run_tournament(series, initial_capital=5000.0)
    assert len(ranked) == 10
    assert ranked[0].tournament_rank == 1
    assert ranked[0].robustness_report is not None


# =====================================================================
# 5. V6 API CONTRACT TESTS (Section 67)
# =====================================================================


@pytest.mark.asyncio
async def test_v6_api_endpoints(client: AsyncClient):
    # 1. Registered Strategies
    res = await client.get("/api/v1/strategies")
    assert res.status_code == 200
    strats = res.json()
    assert len(strats) >= 2

    # 2. Promote Strategy
    promote_res = await client.post(f"/api/v1/strategies/{strats[0]['id']}/promote")
    assert promote_res.status_code == 200
    assert promote_res.json()["status"] == "PAPER_TRADING"

    # 3. Confirmed Divergences
    div_res = await client.get("/api/v1/divergences?symbol=BTC/USDT")
    assert div_res.status_code == 200
    divs = div_res.json()
    assert len(divs) >= 1
    assert divs[0]["divergence_type"] == "REGULAR_BULLISH"

    # 4. Run Strategy Discovery Tournament
    disc_res = await client.post("/api/v1/strategy-discovery/run")
    assert disc_res.status_code == 200
    disc_data = disc_res.json()
    assert disc_data["candidates_count"] == 10
    assert "winner" in disc_data

    # 5. Validation Execution Deviation
    val_res = await client.get("/api/v1/validation/comparison")
    assert val_res.status_code == 200
    assert "execution_efficiency_pct" in val_res.json()
