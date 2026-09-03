from typing import Any, Dict

from shared.enums import MarketRegime
from shared.schemas import FeatureVector, MarketRegimeState


class RegimeDetector:
    """
    Deterministic rule-based Market Regime Engine.
    Detects market condition without look-ahead bias or black-box models.
    """

    def __init__(
        self,
        adx_trend_threshold: float = 23.0,
        adx_ranging_threshold: float = 18.0,
        bb_high_vol_percentile: float = 0.08,  # >8% bandwidth is high volatility
        bb_low_vol_percentile: float = 0.025,  # <2.5% bandwidth is low volatility / compression
    ):
        self.adx_trend_threshold = adx_trend_threshold
        self.adx_ranging_threshold = adx_ranging_threshold
        self.bb_high_vol_percentile = bb_high_vol_percentile
        self.bb_low_vol_percentile = bb_low_vol_percentile

    def detect(self, feature_vector: FeatureVector) -> MarketRegimeState:
        ind = feature_vector.indicators
        symbol = feature_vector.symbol
        tf = feature_vector.timeframe
        ts = feature_vector.timestamp

        # Extract values with safe fallbacks
        close = ind.get("close", 0.0)
        ema_20 = ind.get("ema_20", 0.0)
        ema_50 = ind.get("ema_50", 0.0)
        ema_200 = ind.get("ema_200", 0.0)
        adx = ind.get("adx", 0.0)
        bb_bandwidth = ind.get("bb_bandwidth", 0.0)

        metrics: Dict[str, Any] = {
            "adx": round(adx, 2),
            "bb_bandwidth": round(bb_bandwidth, 4),
            "ema_20": round(ema_20, 2),
            "ema_50": round(ema_50, 2),
            "ema_200": round(ema_200, 2),
        }

        # Check for extreme volatility first
        if bb_bandwidth > self.bb_high_vol_percentile:
            return MarketRegimeState(
                symbol=symbol,
                timeframe=tf,
                timestamp=ts,
                regime=MarketRegime.HIGH_VOLATILITY,
                confidence=min(1.0, bb_bandwidth / 0.12),
                metrics=metrics,
            )

        # Check for volatility squeeze / compression
        if bb_bandwidth < self.bb_low_vol_percentile and adx < self.adx_ranging_threshold:
            return MarketRegimeState(
                symbol=symbol,
                timeframe=tf,
                timestamp=ts,
                regime=MarketRegime.LOW_VOLATILITY,
                confidence=0.85,
                metrics=metrics,
            )

        # Bull Trend: EMA 20 > EMA 50 > EMA 200 and ADX > threshold and Price > EMA 50
        if ema_20 > ema_50 > ema_200 and adx > self.adx_trend_threshold and close > ema_50:
            confidence = min(0.95, 0.5 + (adx / 100.0))
            return MarketRegimeState(
                symbol=symbol,
                timeframe=tf,
                timestamp=ts,
                regime=MarketRegime.BULL_TREND,
                confidence=confidence,
                metrics=metrics,
            )

        # Bear Trend: EMA 20 < EMA 50 < EMA 200 and ADX > threshold and Price < EMA 50
        if ema_20 < ema_50 < ema_200 and adx > self.adx_trend_threshold and close < ema_50:
            confidence = min(0.95, 0.5 + (adx / 100.0))
            return MarketRegimeState(
                symbol=symbol,
                timeframe=tf,
                timestamp=ts,
                regime=MarketRegime.BEAR_TREND,
                confidence=confidence,
                metrics=metrics,
            )

        # Sideways / Ranging: ADX is low or EMAs are tangled
        if adx < self.adx_trend_threshold:
            return MarketRegimeState(
                symbol=symbol,
                timeframe=tf,
                timestamp=ts,
                regime=MarketRegime.SIDEWAYS,
                confidence=0.80,
                metrics=metrics,
            )

        return MarketRegimeState(
            symbol=symbol,
            timeframe=tf,
            timestamp=ts,
            regime=MarketRegime.UNKNOWN,
            confidence=0.50,
            metrics=metrics,
        )
