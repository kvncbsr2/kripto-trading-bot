from dataclasses import dataclass
from typing import Dict, List

from services.feature_engine.features import FeatureEngine
from services.regime_engine.detector import RegimeDetector
from shared.enums import MarketRegime, Timeframe
from shared.schemas import Candle


@dataclass
class MultiTimeframeContext:
    symbol: str
    macro_regime: MarketRegime  # 1D or 4H
    setup_regime: MarketRegime  # 1H
    entry_regime: MarketRegime  # 15m
    trend_aligned: bool
    confidence_multiplier: float


class MultiTimeframeAnalyzer:
    """
    Analyzes market across multiple configurable timeframes:
    e.g. 4H (Macro Trend), 1H (Setup), 15m (Trigger / Entry).
    Provides alignment scores to filter out counter-trend trades.
    """

    def __init__(self):
        self.regime_detector = RegimeDetector()

    def analyze(
        self,
        symbol: str,
        candles_by_timeframe: Dict[Timeframe, List[Candle]],
    ) -> MultiTimeframeContext:
        # Default regimes
        macro_regime = MarketRegime.UNKNOWN
        setup_regime = MarketRegime.UNKNOWN
        entry_regime = MarketRegime.UNKNOWN

        # 4H or 1D for macro
        macro_candles = candles_by_timeframe.get(Timeframe.H4) or candles_by_timeframe.get(
            Timeframe.D1
        )
        if macro_candles and len(macro_candles) >= 30:
            feat_macro = FeatureEngine.get_latest_feature_vector(
                macro_candles, symbol, Timeframe.H4
            )
            if feat_macro:
                macro_regime = self.regime_detector.detect(feat_macro).regime

        # 1H for setup
        setup_candles = candles_by_timeframe.get(Timeframe.H1)
        if setup_candles and len(setup_candles) >= 30:
            feat_setup = FeatureEngine.get_latest_feature_vector(
                setup_candles, symbol, Timeframe.H1
            )
            if feat_setup:
                setup_regime = self.regime_detector.detect(feat_setup).regime

        # 15m for entry
        entry_candles = candles_by_timeframe.get(Timeframe.M15)
        if entry_candles and len(entry_candles) >= 30:
            feat_entry = FeatureEngine.get_latest_feature_vector(
                entry_candles, symbol, Timeframe.M15
            )
            if feat_entry:
                entry_regime = self.regime_detector.detect(feat_entry).regime

        # Alignment calculation
        is_bullish_aligned = macro_regime == MarketRegime.BULL_TREND and entry_regime in [
            MarketRegime.BULL_TREND,
            MarketRegime.SIDEWAYS,
        ]
        is_bearish_aligned = macro_regime == MarketRegime.BEAR_TREND and entry_regime in [
            MarketRegime.BEAR_TREND,
            MarketRegime.SIDEWAYS,
        ]
        trend_aligned = is_bullish_aligned or is_bearish_aligned

        multiplier = 1.2 if trend_aligned else 0.8

        return MultiTimeframeContext(
            symbol=symbol,
            macro_regime=macro_regime,
            setup_regime=setup_regime,
            entry_regime=entry_regime,
            trend_aligned=trend_aligned,
            confidence_multiplier=multiplier,
        )
