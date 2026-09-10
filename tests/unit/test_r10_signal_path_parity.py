from datetime import datetime, timezone
import numpy as np
import pandas as pd
import pytest

from services.strategy_engine.strategies.r10_rsi_divergence import R10RSIDivergenceStrategy
from shared.enums import MarketRegime, SignalDirection, Timeframe
from shared.schemas import FeatureVector, MarketRegimeState


def generate_bullish_divergence_df(length: int = 60) -> pd.DataFrame:
    dates = pd.date_range(start="2026-01-01", periods=length, freq="15min")
    closes = np.linspace(100, 50, length).copy()
    closes[20] = 30.0
    closes[45] = 20.0
    for i in range(46, length):
        closes[i] = 22.0 + (i - 46) * 0.5

    df = pd.DataFrame({
        "timestamp": dates,
        "open": closes - 0.5,
        "high": closes + 1.0,
        "low": closes - 1.0,
        "close": closes,
        "volume": 1000.0,
    })
    return df


def test_r10_path_parity_with_dataframe_metadata():
    strat = R10RSIDivergenceStrategy(rsi_length=14, left_bars=5, right_bars=5)
    df = generate_bullish_divergence_df(60)

    sig_direct = strat.evaluate_from_dataframe(df, symbol="BTC/USDT")

    features = FeatureVector(
        symbol="BTC/USDT",
        timeframe=Timeframe.M15,
        timestamp=datetime.now(timezone.utc),
        indicators={"close": float(df["close"].iloc[-1]), "atr": 1.0},
        metadata={"dataframe": df},
    )
    regime = MarketRegimeState(
        symbol="BTC/USDT",
        timeframe=Timeframe.M15,
        timestamp=datetime.now(timezone.utc),
        regime=MarketRegime.BULL_TREND,
        confidence=0.8,
    )

    sig_via_eval = strat.evaluate(features, regime)

    if sig_direct is None:
        assert sig_via_eval is None
    else:
        assert sig_via_eval is not None
        assert sig_via_eval.direction == sig_direct.direction
        assert sig_via_eval.entry_price == sig_direct.entry_price
        assert sig_via_eval.stop_price == sig_direct.stop_price
        assert sig_via_eval.take_profit == sig_direct.take_profit
        assert sig_via_eval.strategy == sig_direct.strategy


def test_r10_evaluate_precomputed_features_fallback():
    strat = R10RSIDivergenceStrategy(min_signal_score=70.0)
    features = FeatureVector(
        symbol="BTC/USDT",
        timeframe=Timeframe.M15,
        timestamp=datetime.now(timezone.utc),
        indicators={
            "close": 60000.0,
            "atr": 300.0,
            "bullish_divergence": 1.0,
            "divergence_score": 18.0,
        },
    )
    regime = MarketRegimeState(
        symbol="BTC/USDT",
        timeframe=Timeframe.M15,
        timestamp=datetime.now(timezone.utc),
        regime=MarketRegime.BULL_TREND,
        confidence=0.8,
    )
    sig = strat.evaluate(features, regime)
    assert sig is not None
    assert sig.direction == SignalDirection.LONG
