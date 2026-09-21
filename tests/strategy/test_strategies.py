from datetime import datetime, timezone

from services.strategy_engine.strategies.mean_reversion import MeanReversionStrategy
from services.strategy_engine.strategies.rsi_divergence import RSIDivergenceStrategy
from services.strategy_engine.strategies.trend_following import TrendFollowingStrategy
from shared.enums import MarketRegime, SignalDirection, Timeframe
from shared.schemas import FeatureVector, MarketRegimeState


def test_trend_following_strategy_long():
    strat = TrendFollowingStrategy()
    features = FeatureVector(
        symbol="BTC/USDT",
        timeframe=Timeframe.M15,
        timestamp=datetime.now(timezone.utc),
        indicators={
            "close": 60000.0,
            "ema_20": 60500.0,
            "ema_50": 59500.0,
            "ema_200": 58000.0,
            "adx": 30.0,
            "rsi": 55.0,
            "atr": 400.0,
        },
    )
    regime = MarketRegimeState(
        symbol="BTC/USDT",
        timeframe=Timeframe.M15,
        timestamp=datetime.now(timezone.utc),
        regime=MarketRegime.BULL_TREND,
        confidence=0.9,
    )
    sig = strat.evaluate(features, regime)
    assert sig is not None
    assert sig.direction == SignalDirection.LONG
    assert sig.stop_price < sig.entry_price < sig.take_profit


def test_mean_reversion_veto_in_trending_market():
    strat = MeanReversionStrategy()
    features = FeatureVector(
        symbol="BTC/USDT",
        timeframe=Timeframe.M15,
        timestamp=datetime.now(timezone.utc),
        indicators={
            "close": 58000.0,
            "bb_lower": 58200.0,
            "bb_middle": 60000.0,
            "bb_upper": 62000.0,
            "rsi": 25.0,
            "atr": 400.0,
        },
    )
    # Market is in strong BEAR_TREND, MeanReversion MUST veto
    regime = MarketRegimeState(
        symbol="BTC/USDT",
        timeframe=Timeframe.M15,
        timestamp=datetime.now(timezone.utc),
        regime=MarketRegime.BEAR_TREND,
        confidence=0.9,
    )
    sig = strat.evaluate(features, regime)
    assert sig is None


def test_rsi_divergence_strategy_bullish():
    strat = RSIDivergenceStrategy(min_score=50.0)
    features = FeatureVector(
        symbol="BTC/USDT",
        timeframe=Timeframe.M15,
        timestamp=datetime.now(timezone.utc),
        indicators={
            "close": 60000.0,
            "atr": 300.0,
            "bullish_divergence": 1.0,
            "divergence_score": 18.0,
            "ema_20": 60100.0,
            "ema_50": 60050.0,
            "ema_200": 59900.0,
            "macd_histogram": 2.0,
            "adx": 22.0,
            "rsi": 44.0,
            "volume_ratio": 1.3,
            "bb_bandwidth": 0.04,
            "bullish_structure": 1.0,
        },
    )
    regime = MarketRegimeState(
        symbol="BTC/USDT",
        timeframe=Timeframe.M15,
        timestamp=datetime.now(timezone.utc),
        regime=MarketRegime.SIDEWAYS,
        confidence=0.8,
    )
    sig = strat.evaluate(features, regime)
    assert sig is not None
    assert sig.direction == SignalDirection.LONG
    assert sig.strategy == "rsi_divergence"


def test_evaluate_from_dataframe_adapter():
    import numpy as np
    import pandas as pd

    # Generate synthetic 50 bars
    dates = pd.date_range(end=datetime.now(timezone.utc), periods=50, freq="15min")
    prices = np.linspace(100, 105, 50) + np.random.normal(0, 0.5, 50)
    df = pd.DataFrame({
        "timestamp": dates,
        "open": prices - 0.2,
        "high": prices + 0.5,
        "low": prices - 0.5,
        "close": prices,
        "volume": [100.0] * 50,
    })

    tf_strat = TrendFollowingStrategy()
    mr_strat = MeanReversionStrategy()
    rsi_strat = RSIDivergenceStrategy()

    # Verify each strategy handles DataFrame without exceptions
    sig_tf = tf_strat.evaluate_from_dataframe(df, "BTC/USDT")
    assert sig_tf is None or hasattr(sig_tf, "direction")

    sig_mr = mr_strat.evaluate_from_dataframe(df, "BTC/USDT")
    assert sig_mr is None or hasattr(sig_mr, "direction")

    sig_rsi = rsi_strat.evaluate_from_dataframe(df, "BTC/USDT")
    assert sig_rsi is None or hasattr(sig_rsi, "direction")

