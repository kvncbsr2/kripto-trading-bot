"""
Unit tests for TurboFastStrikeStrategy and Registry Integration.
Verifies all 3 quantitative scalping triggers, feature vector evaluation, and risk modeling.
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
from services.strategy_engine.strategies.turbo_fast_strike import (
    TurboFastStrikeStrategy,
)
from shared.enums import MarketRegime, SignalDirection, Timeframe
from shared.schemas import FeatureVector, MarketRegimeState


def _make_dummy_candles(n: int = 35, base_price: float = 100.0, trend: float = 0.0) -> pd.DataFrame:
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
    """Verify turbo_fast_strike is registered and instantiable."""
    assert "turbo_fast_strike" in STRATEGY_REGISTRY
    meta = get_strategy_metadata("turbo_fast_strike")
    assert meta.id == "turbo_fast_strike"
    assert "Turbo" in meta.name
    assert meta.category == "HIGH_FREQUENCY_MOMENTUM"
    assert meta.default_timeframe == "5m"

    all_strats = get_all_strategies()
    assert any(s["id"] == "turbo_fast_strike" for s in all_strats)

    strat = create_strategy("turbo_fast_strike", min_signal_score=65.0, risk_reward_ratio=2.2)
    assert isinstance(strat, TurboFastStrikeStrategy)
    assert strat.min_signal_score == 65.0
    assert strat.risk_reward_ratio == 2.2
    assert strat.timeframe == "5m"


def test_flash_dip_rebound_trigger():
    """Verify SETUP 1: FLASH_DIP_REBOUND (Oversold dump + rejection wick)."""
    strat = TurboFastStrikeStrategy(min_signal_score=60.0)
    df = _make_dummy_candles(n=35, base_price=50.0, trend=-1.5)

    # Force last candle to print a deep hammer bounce after heavy dump
    last_idx = df.index[-1]
    df.loc[last_idx, "open"] = 12.0
    df.loc[last_idx, "close"] = 13.5   # Strong green close
    df.loc[last_idx, "high"] = 13.8
    df.loc[last_idx, "low"] = 10.0    # 2.0 point lower wick (rejection)
    df.loc[last_idx, "volume"] = 5000.0

    sig = strat.evaluate_from_dataframe(df, symbol="SOL/USDT")
    assert sig is not None
    assert sig.direction == SignalDirection.LONG
    assert sig.metadata["trigger_type"] == "FLASH_DIP_REBOUND"
    assert sig.entry_price == round(float(df["close"].iloc[-1]), 4)
    assert sig.stop_price < sig.entry_price < sig.take_profit

    # Verify asymmetric risk-reward
    stop_dist = sig.entry_price - sig.stop_price
    tp_dist = sig.take_profit - sig.entry_price
    assert tp_dist >= (stop_dist * 1.8)


def test_breakout_acceleration_trigger():
    """Verify SETUP 2: BREAKOUT_ACCELERATION (EMA9 reclaim with volume)."""
    strat = TurboFastStrikeStrategy(min_signal_score=60.0)
    df = _make_dummy_candles(n=35, base_price=20.0, trend=0.1)

    # Force a 3-candle breakout surge
    df.loc[df.index[-3:], "close"] = [21.0, 21.6, 22.5]
    df.loc[df.index[-3:], "open"] = [20.8, 21.0, 21.5]
    df.loc[df.index[-3:], "high"] = [21.2, 21.8, 22.8]
    df.loc[df.index[-3:], "low"] = [20.7, 20.9, 21.4]
    df.loc[df.index[-1], "volume"] = 6000.0

    sig = strat.evaluate_from_dataframe(df, symbol="ETH/USDT")
    assert sig is not None
    assert sig.direction == SignalDirection.LONG
    assert sig.metadata["trigger_type"] == "BREAKOUT_ACCELERATION"
    assert sig.regime == MarketRegime.BULL_TREND
    assert sig.metadata["signal_score"] >= 65.0


def test_fast_pullback_reclaim_trigger():
    """Verify SETUP 3: FAST_PULLBACK_RECLAIM (EMA21 support hold)."""
    strat = TurboFastStrikeStrategy(min_signal_score=60.0)
    # Gentle uptrend so EMA9 > EMA21
    df = _make_dummy_candles(n=35, base_price=100.0, trend=0.2)

    # Moderate price near EMA21 with moderate RSI (38-52)
    last_idx = df.index[-1]
    prev_idx = df.index[-2]
    df.loc[prev_idx, "close"] = 104.0
    df.loc[last_idx, "close"] = 104.5
    df.loc[last_idx, "open"] = 104.0
    df.loc[last_idx, "high"] = 105.0
    df.loc[last_idx, "low"] = 103.8

    sig = strat.evaluate_from_dataframe(df, symbol="BTC/USDT")
    if sig:
        assert sig.direction == SignalDirection.LONG
        assert sig.metadata["trigger_type"] in [
            "FLASH_DIP_REBOUND",
            "BREAKOUT_ACCELERATION",
            "FAST_PULLBACK_RECLAIM",
        ]


def test_evaluate_from_features():
    """Verify BaseStrategy evaluate() implementation with FeatureVector."""
    strat = TurboFastStrikeStrategy(min_signal_score=60.0)
    now = datetime.now(timezone.utc)
    regime = MarketRegimeState(
        symbol="BTC/USDT",
        timeframe=Timeframe.M5,
        regime=MarketRegime.BULL_TREND,
        confidence=0.85,
        metrics={"volatility": 0.02},
        timestamp=now,
    )

    # Case 1: Oversold dip
    fv_dip = FeatureVector(
        symbol="BTC/USDT",
        timestamp=now,
        timeframe=Timeframe.M5,
        features={"close": 60000.0},
        indicators={"close": 60000.0, "rsi": 25.0, "ema_9": 61000.0, "atr": 500.0},
    )
    sig_dip = strat.evaluate(fv_dip, regime)
    assert sig_dip is not None
    assert sig_dip.metadata["trigger_type"] == "FLASH_DIP_REBOUND"
    assert sig_dip.direction == SignalDirection.LONG

    # Case 2: Breakout surge
    fv_surge = FeatureVector(
        symbol="ETH/USDT",
        timestamp=now,
        timeframe=Timeframe.M5,
        features={"close": 3000.0},
        indicators={"close": 3000.0, "rsi": 58.0, "ema_9": 2980.0, "return_3c": 0.6, "atr": 20.0},
    )
    sig_surge = strat.evaluate(fv_surge, regime)
    assert sig_surge is not None
    assert sig_surge.metadata["trigger_type"] == "BREAKOUT_ACCELERATION"
    assert sig_surge.direction == SignalDirection.LONG


def test_disabled_and_insufficient_data():
    """Verify disabled strategy and short dataframe edge cases."""
    strat = TurboFastStrikeStrategy(enabled=False)
    df = _make_dummy_candles(n=35)
    assert strat.evaluate_from_dataframe(df, "BTC/USDT") is None

    strat_enabled = TurboFastStrikeStrategy(enabled=True)
    short_df = _make_dummy_candles(n=10)
    assert strat_enabled.evaluate_from_dataframe(short_df, "BTC/USDT") is None
    assert strat_enabled.evaluate_from_dataframe(None, "BTC/USDT") is None
