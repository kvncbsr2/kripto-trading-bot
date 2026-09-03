from datetime import datetime, timezone

from services.signal_engine.scorer import SignalScorer
from shared.enums import MarketRegime, SignalDirection, Timeframe
from shared.schemas import FeatureVector, MarketRegimeState


def test_signal_scorer_confluence():
    features = FeatureVector(
        symbol="BTC/USDT",
        timeframe=Timeframe.M15,
        timestamp=datetime.now(timezone.utc),
        indicators={
            "ema_20": 61000.0,
            "ema_50": 60000.0,
            "ema_200": 58000.0,
            "adx": 30.0,
            "rsi": 55.0,
            "macd_histogram": 5.0,
            "volume_ratio": 1.6,
            "bullish_structure": 1.0,
            "bb_bandwidth": 0.04,
        },
    )
    regime = MarketRegimeState(
        symbol="BTC/USDT",
        timeframe=Timeframe.M15,
        timestamp=datetime.now(timezone.utc),
        regime=MarketRegime.BULL_TREND,
        confidence=0.85,
    )
    score, category, subscores = SignalScorer.score_signal(
        features, regime, SignalDirection.LONG, has_divergence=False
    )
    assert score >= 70.0
    assert category in ["GOOD", "HIGH_QUALITY"]
    assert "trend" in subscores
    assert "momentum" in subscores


def test_opportunity_score_calculation():
    features = FeatureVector(
        symbol="BTC/USDT",
        timeframe=Timeframe.M15,
        timestamp=datetime.now(timezone.utc),
        indicators={
            "adx": 28.0,
            "volume_ratio": 1.4,
            "bb_bandwidth": 0.05,
        },
    )
    regime = MarketRegimeState(
        symbol="BTC/USDT",
        timeframe=Timeframe.M15,
        timestamp=datetime.now(timezone.utc),
        regime=MarketRegime.BULL_TREND,
        confidence=0.8,
    )
    opp_score, label = SignalScorer.calculate_opportunity_score(features, regime)
    assert 0.0 <= opp_score <= 100.0
    assert label in ["IGNORE", "WATCH", "CONSIDER", "TRADE", "HIGH_CONVICTION"]
