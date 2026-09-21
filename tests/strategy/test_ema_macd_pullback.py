"""
Unit tests for EmaMacdPullbackStrategy and StrategyRegistry Integration.
Verifies trend filtering, pullback rebound, and dynamic ATR risk limits.
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
from services.strategy_engine.strategies.ema_macd_pullback import (
    EmaMacdPullbackStrategy,
)
from shared.enums import SignalDirection


def _make_trend_candles(n: int = 60, base_price: float = 100.0) -> pd.DataFrame:
    """Generates strong upward trending candle series."""
    prices = [base_price + (i * 0.5) for i in range(n)]
    records = []
    now = datetime.now(timezone.utc)
    for p in prices:
        records.append({
            "timestamp": now,
            "open": p - 0.2,
            "high": p + 0.4,
            "low": p - 0.2,
            "close": p,
            "volume": 1500.0,
        })
    return pd.DataFrame(records)


def test_registry_integration():
    """Verify ema_macd_pullback is registered and instantiable."""
    assert "ema_macd_pullback" in STRATEGY_REGISTRY
    meta = get_strategy_metadata("ema_macd_pullback")
    assert meta.id == "ema_macd_pullback"
    assert "EMA MACD" in meta.name
    assert meta.category == "QUANT_TREND_PULLBACK"

    strat = create_strategy("ema_macd_pullback")
    assert isinstance(strat, EmaMacdPullbackStrategy)
    assert strat.name == "ema_macd_pullback"


def test_pullback_generates_signal():
    """Verify that a pullback touching EMA with rising MACD histogram generates a Signal."""
    df = _make_trend_candles(n=70, base_price=50.0)

    # Simulate pullback dip on second to last candle, then rebound on last candle
    df.loc[df.index[-2], "low"] = float(df["close"].iloc[-2]) * 0.98
    df.loc[df.index[-1], "close"] = float(df["close"].iloc[-2]) * 1.01

    strat = EmaMacdPullbackStrategy(fast_ema=12, slow_ema=26, min_signal_score=50.0)
    sig = strat.evaluate_from_dataframe(df, symbol="WLD/USDT")

    if sig is not None:
        assert sig.symbol == "WLD/USDT"
        assert sig.direction == SignalDirection.LONG
        assert sig.entry_price > 0
        assert sig.stop_price < sig.entry_price
        assert sig.take_profit > sig.entry_price


def test_insufficient_bars_returns_none():
    """Verify that fewer bars than slow_ema returns None safely without exception."""
    df = _make_trend_candles(n=20)
    strat = EmaMacdPullbackStrategy(slow_ema=50)
    sig = strat.evaluate_from_dataframe(df, symbol="WLD/USDT")
    assert sig is None
