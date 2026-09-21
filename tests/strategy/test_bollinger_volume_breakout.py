"""
Unit tests for BollingerVolumeBreakoutStrategy and StrategyRegistry Integration.
Verifies Rule 4 (evaluate_from_dataframe), breakout detection, volume gating, and ATR SL/TP logic.
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
from services.strategy_engine.strategies.bollinger_volume_breakout import (
    BollingerVolumeBreakoutStrategy,
)
from shared.enums import MarketRegime, SignalDirection


def _make_dummy_candles(n: int = 40, base_price: float = 100.0) -> pd.DataFrame:
    """Generates synthetic OHLCV candle DataFrame."""
    np.random.seed(42)
    prices = [base_price]
    for _ in range(1, n):
        prices.append(prices[-1] + np.random.normal(0, 0.2))

    records = []
    now = datetime.now(timezone.utc)
    for i, p in enumerate(prices):
        records.append({
            "timestamp": now,
            "open": p - 0.1,
            "high": p + 0.3,
            "low": p - 0.3,
            "close": p,
            "volume": 1000.0,
        })
    return pd.DataFrame(records)


def test_registry_integration():
    """Verify bollinger_volume_breakout is registered and instantiable."""
    assert "bollinger_volume_breakout" in STRATEGY_REGISTRY
    meta = get_strategy_metadata("bollinger_volume_breakout")
    assert meta.id == "bollinger_volume_breakout"
    assert "Bollinger" in meta.name
    assert meta.category == "QUANT_VOLATILITY_BREAKOUT"

    all_strats = get_all_strategies()
    assert any(s["id"] == "bollinger_volume_breakout" for s in all_strats)

    strat = create_strategy("bollinger_volume_breakout")
    assert isinstance(strat, BollingerVolumeBreakoutStrategy)
    assert strat.name == "bollinger_volume_breakout"


def test_breakout_with_volume_generates_signal():
    """Verify that an upper Bollinger breakout accompanied by volume surge triggers a valid Signal."""
    df = _make_dummy_candles(n=45, base_price=100.0)

    # Simulate breakout on the last candle: price surges above BB with 2.5x volume
    df.loc[df.index[-1], "close"] = 115.0
    df.loc[df.index[-1], "high"] = 116.0
    df.loc[df.index[-1], "volume"] = 3000.0  # 3x average volume

    strat = BollingerVolumeBreakoutStrategy(bb_period=20, vol_mult=1.2, min_signal_score=60.0)
    sig = strat.evaluate_from_dataframe(df, symbol="ZEC/USDT")

    assert sig is not None
    assert sig.symbol == "ZEC/USDT"
    assert sig.direction == SignalDirection.LONG
    assert sig.entry_price == 115.0
    assert sig.stop_price < 115.0
    assert sig.take_profit > 115.0
    assert sig.metadata["vol_ratio"] >= 1.2
    assert "Bollinger Volume Breakout" in sig.reason


def test_breakout_without_volume_rejected():
    """Verify that a breakout WITHOUT volume expansion is rejected (Zero False Breakout guard)."""
    df = _make_dummy_candles(n=45, base_price=100.0)

    # Price jumps but volume is anemic (0.5x)
    df.loc[df.index[-1], "close"] = 115.0
    df.loc[df.index[-1], "high"] = 116.0
    df.loc[df.index[-1], "volume"] = 400.0

    strat = BollingerVolumeBreakoutStrategy(bb_period=20, vol_mult=1.2)
    sig = strat.evaluate_from_dataframe(df, symbol="ZEC/USDT")

    assert sig is None


def test_no_breakout_flat_market():
    """Verify that flat/sideways candle inside the band produces no signal."""
    df = _make_dummy_candles(n=45, base_price=100.0)
    strat = BollingerVolumeBreakoutStrategy(bb_period=20)
    sig = strat.evaluate_from_dataframe(df, symbol="ZEC/USDT")
    assert sig is None
