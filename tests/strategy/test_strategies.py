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
