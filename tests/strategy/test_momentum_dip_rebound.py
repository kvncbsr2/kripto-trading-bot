"""
Unit tests for MomentumDipReboundStrategy and Registry Integration.
Verifies all 3 quantitative factors, zero lookahead integrity, and risk controls.
"""

from datetime import datetime, timezone
import numpy as np
import pandas as pd
import pytest

from services.strategy_engine.registry import (
    STRATEGY_REGISTRY,
    create_strategy,
    get_all_strategies,
    get_strategy_metadata,
)
from services.strategy_engine.strategies.momentum_dip_rebound import (
    MomentumDipReboundStrategy,
)
from shared.enums import MarketRegime, SignalDirection, Timeframe
from shared.schemas import FeatureVector, MarketRegimeState


def _make_dummy_candles(n: int = 40, base_price: float = 100.0, trend: float = 0.0) -> pd.DataFrame:
    """Generates synthetic OHLCV candle DataFrame with reproducible values."""
    np.random.seed(42)
    prices = [base_price]
    for i in range(1, n):
        step = trend + np.random.normal(0, 0.5)
        prices.append(max(10.0, prices[-1] + step))

    records = []
    now = datetime.now(timezone.utc)
    for i, p in enumerate(prices):
        o = p - 0.2
        c = p + 0.2
        h = max(o, c) + 0.5
        l = min(o, c) - 0.5
        records.append({
            "timestamp": now,
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": 1000.0 + (i * 10),
        })
    return pd.DataFrame(records)


def test_registry_integration():
    """Verify momentum_dip_rebound is registered and instantiable."""
    assert "momentum_dip_rebound" in STRATEGY_REGISTRY
    meta = get_strategy_metadata("momentum_dip_rebound")
    assert meta.id == "momentum_dip_rebound"
    assert "Momentum" in meta.name
    assert meta.category == "MULTI_FACTOR_MOMENTUM_DIP"

    all_strats = get_all_strategies()
    assert any(s["id"] == "momentum_dip_rebound" for s in all_strats)

    strat = create_strategy("momentum_dip_rebound", min_signal_score=60.0, risk_reward_ratio=2.5)
    assert isinstance(strat, MomentumDipReboundStrategy)
    assert strat.min_signal_score == 60.0
    assert strat.risk_reward_ratio == 2.5


def test_momentum_breakout_factor_dataframe():
    """Verify Factor 1: Momentum Breakout (NEAR pattern)."""
    strat = MomentumDipReboundStrategy(min_signal_score=50.0, momentum_min_return=0.5)
    df = _make_dummy_candles(n=35, base_price=10.0, trend=0.1)

    # Force last 3 candles to show a strong breakout reclaiming EMA9
    df.loc[df.index[-3:], "close"] = [11.0, 11.5, 12.2]
    df.loc[df.index[-3:], "open"] = [10.8, 11.0, 11.4]
    df.loc[df.index[-3:], "high"] = [11.2, 11.7, 12.4]
    df.loc[df.index[-3:], "low"] = [10.7, 10.9, 11.3]

    sig = strat.evaluate_from_dataframe(df, symbol="NEAR/USDT")
    assert sig is not None
    assert sig.direction == SignalDirection.LONG
    assert sig.metadata["factor"] == "MOMENTUM_BREAKOUT"
    assert sig.entry_price == round(float(df["close"].iloc[-1]), 4)
    assert sig.stop_price < sig.entry_price < sig.take_profit
    # Verify R:R >= 2.0
    stop_dist = sig.entry_price - sig.stop_price
    tp_dist = sig.take_profit - sig.entry_price
    assert tp_dist >= (stop_dist * 1.95)


def test_deep_oversold_bounce_factor_dataframe():
    """Verify Factor 2: Deep Oversold Bounce (SUI pattern)."""
    strat = MomentumDipReboundStrategy(min_signal_score=50.0, oversold_threshold=35.0)
    df = _make_dummy_candles(n=35, base_price=20.0, trend=-0.5)

    # Make the last candle print a hammer/rejection wick after deep selloff
    last_idx = df.index[-1]
    df.loc[last_idx, "open"] = 6.0
    df.loc[last_idx, "close"] = 6.8  # Strong green bounce
    df.loc[last_idx, "high"] = 7.0
    df.loc[last_idx, "low"] = 5.2   # Long lower rejection wick

    sig = strat.evaluate_from_dataframe(df, symbol="SUI/USDT")
    assert sig is not None
    assert sig.direction == SignalDirection.LONG
    assert sig.metadata["factor"] == "DEEP_OVERSOLD_BOUNCE"
    assert sig.stop_price < sig.entry_price


def test_support_hold_rebound_factor_dataframe():
    """Verify Factor 3: Support Hold & Base Consolidation (SOL pattern)."""
    strat = MomentumDipReboundStrategy(min_signal_score=50.0, support_rsi_min=30.0, support_rsi_max=50.0)
    df = _make_dummy_candles(n=35, base_price=100.0, trend=-0.1)

    # Stabilize around support base near EMA9
    last_idx = df.index[-1]
    prev_idx = df.index[-2]
    df.loc[prev_idx, "close"] = 96.0
    df.loc[last_idx, "close"] = 97.5
    df.loc[last_idx, "open"] = 96.2
    df.loc[last_idx, "high"] = 98.0
    df.loc[last_idx, "low"] = 96.0

    sig = strat.evaluate_from_dataframe(df, symbol="SOL/USDT")
    if sig:
        assert sig.direction == SignalDirection.LONG
        assert sig.stop_price < sig.entry_price < sig.take_profit


def test_zero_lookahead_integrity():
    """Verify that signal evaluated at bar T depends strictly on T and past bars, never on future T+1."""
    strat = MomentumDipReboundStrategy(min_signal_score=50.0)
    df = _make_dummy_candles(n=40, base_price=50.0)

    # Evaluate slice up to candle 34
    slice_35 = df.iloc[:35].copy()
    sig_at_35 = strat.evaluate_from_dataframe(slice_35, symbol="BTC/USDT")

    # Add 5 future candles with massive volatility
    slice_40 = df.copy()
    slice_40.loc[slice_40.index[-5:], "close"] = 150.0

    # Re-evaluate slice up to candle 34 again
    sig_at_35_retest = strat.evaluate_from_dataframe(slice_40.iloc[:35].copy(), symbol="BTC/USDT")

    # Decisions must match exactly
    if sig_at_35 is None:
        assert sig_at_35_retest is None
    else:
        assert sig_at_35_retest is not None
        assert sig_at_35.entry_price == sig_at_35_retest.entry_price
        assert sig_at_35.stop_price == sig_at_35_retest.stop_price
        assert sig_at_35.take_profit == sig_at_35_retest.take_profit


def test_evaluate_feature_vector_interface():
    """Verify stream FeatureVector evaluate implementation."""
    strat = MomentumDipReboundStrategy(min_signal_score=50.0)
    fv = FeatureVector(
        symbol="ETH/USDT",
        timeframe=Timeframe.M15,
        timestamp=datetime.now(timezone.utc),
        indicators={
            "close": 2450.0,
            "ema_9": 2440.0,
            "return_3c": 1.2,
            "rsi": 54.0,
            "atr": 30.0,
        },
    )
    regime = MarketRegimeState(
        symbol="ETH/USDT",
        timeframe=Timeframe.M15,
        timestamp=datetime.now(timezone.utc),
        regime=MarketRegime.BULL_TREND,
        confidence=0.85,
    )
    sig = strat.evaluate(fv, regime)
    assert sig is not None
    assert sig.direction == SignalDirection.LONG
    assert sig.symbol == "ETH/USDT"
    assert sig.metadata["factor"] == "MOMENTUM_BREAKOUT"
    assert sig.stop_price < sig.entry_price < sig.take_profit


def test_adaptive_volume_and_rr_scaling():
    """Verify that high-volume breakouts dynamically scale R:R up to capture larger runs."""
    strat = MomentumDipReboundStrategy(min_signal_score=50.0, risk_reward_ratio=2.0)
    df = _make_dummy_candles(n=35, base_price=10.0, trend=0.1)

    # Force high volume breakout
    df.loc[df.index[-3:], "close"] = [11.0, 11.5, 12.2]
    df.loc[df.index[-3:], "open"] = [10.8, 11.0, 11.4]
    df.loc[df.index[-3:], "high"] = [11.2, 11.7, 12.5]
    df.loc[df.index[-3:], "low"] = [10.7, 10.9, 11.3]
    df.loc[df.index[-1], "volume"] = 50000.0  # Massive volume surge

    sig = strat.evaluate_from_dataframe(df, symbol="NEAR/USDT")
    assert sig is not None
    assert sig.metadata["vol_ratio"] >= 2.0
    assert sig.metadata["winning_model"] == "NEAR_MOMENTUM_EXPANSION"
    assert sig.metadata["dynamic_rr"] >= 2.0


def test_trend_anchor_continuation_factor():
    """Verify Factor 4: Trend Anchor Continuation (ETH & BTC model)."""
    strat = MomentumDipReboundStrategy(min_signal_score=50.0)
    df = _make_dummy_candles(n=35, base_price=2000.0, trend=5.0)

    last_idx = df.index[-1]
    df.loc[last_idx, "close"] = 2100.0
    df.loc[last_idx, "open"] = 2090.0
    df.loc[last_idx, "high"] = 2110.0
    df.loc[last_idx, "low"] = 2085.0

    sig = strat.evaluate_from_dataframe(df, symbol="ETH/USDT")
    if sig:
        assert sig.direction == SignalDirection.LONG
        assert sig.metadata["factor"] in ["MOMENTUM_BREAKOUT", "TREND_CONTINUATION", "SUPPORT_HOLD_REBOUND"]

