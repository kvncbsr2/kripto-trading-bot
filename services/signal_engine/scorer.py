from typing import Dict, Tuple

from shared.enums import MarketRegime, SignalDirection
from shared.schemas import FeatureVector, MarketRegimeState


class SignalScorer:
    """
    Evaluates signal confidence on a 0 - 100 point scale:
    - Trend (max 20 pts)
    - Momentum (max 15 pts)
    - RSI Divergence (max 20 pts)
    - Volume (max 10 pts)
    - Market Regime (max 15 pts)
    - Structure (max 10 pts)
    - Volatility (max 10 pts)
    """

    @classmethod
    def score_signal(
        cls,
        features: FeatureVector,
        regime_state: MarketRegimeState,
        direction: SignalDirection,
        has_divergence: bool = False,
    ) -> Tuple[float, str, Dict[str, float]]:
        ind = features.indicators
        scores: Dict[str, float] = {}

        # 1. Trend (max 20)
        trend_score = 0.0
        ema_20 = ind.get("ema_20", 0.0)
        ema_50 = ind.get("ema_50", 0.0)
        ema_200 = ind.get("ema_200", 0.0)
        adx = ind.get("adx", 0.0)

        if direction == SignalDirection.LONG:
            if ema_20 > ema_50 > ema_200:
                trend_score += 12.0
            elif ema_20 > ema_50:
                trend_score += 6.0
        elif direction == SignalDirection.SHORT:
            if ema_20 < ema_50 < ema_200:
                trend_score += 12.0
            elif ema_20 < ema_50:
                trend_score += 6.0

        if adx >= 25.0:
            trend_score += 8.0
        elif adx >= 20.0:
            trend_score += 4.0
        scores["trend"] = min(20.0, trend_score)

        # 2. Momentum (max 15)
        mom_score = 0.0
        rsi = ind.get("rsi", 50.0)
        macd_hist = ind.get("macd_histogram", 0.0)

        if direction == SignalDirection.LONG:
            if 40.0 <= rsi <= 65.0:
                mom_score += 8.0
            elif rsi < 35.0:
                mom_score += 5.0
            if macd_hist > 0:
                mom_score += 7.0
        elif direction == SignalDirection.SHORT:
            if 35.0 <= rsi <= 60.0:
                mom_score += 8.0
            elif rsi > 65.0:
                mom_score += 5.0
            if macd_hist < 0:
                mom_score += 7.0
        scores["momentum"] = min(15.0, mom_score)

        # 3. RSI Divergence (max 20)
        div_score = 0.0
        if has_divergence:
            div_score = ind.get("divergence_score", 15.0)
        scores["divergence"] = min(20.0, div_score)

        # 4. Volume (max 10)
        vol_score = 0.0
        vol_ratio = ind.get("volume_ratio", 1.0)
        if vol_ratio >= 1.5:
            vol_score = 10.0
        elif vol_ratio >= 1.1:
            vol_score = 6.0
        elif vol_ratio >= 0.8:
            vol_score = 3.0
        scores["volume"] = vol_score

        # 5. Market Regime (max 15)
        regime_score = 0.0
        regime = regime_state.regime
        if direction == SignalDirection.LONG and regime == MarketRegime.BULL_TREND:
            regime_score = 15.0
        elif direction == SignalDirection.SHORT and regime == MarketRegime.BEAR_TREND:
            regime_score = 15.0
        elif regime in [MarketRegime.SIDEWAYS, MarketRegime.LOW_VOLATILITY]:
            regime_score = 10.0
        scores["regime"] = regime_score

        # 6. Structure (max 10)
        struct_score = 0.0
        if direction == SignalDirection.LONG and ind.get("bullish_structure", 0.0) > 0.5:
            struct_score = 10.0
        elif direction == SignalDirection.SHORT and ind.get("bearish_structure", 0.0) > 0.5:
            struct_score = 10.0
        scores["structure"] = struct_score

        # 7. Volatility (max 10)
        volat_score = 0.0
        bb_width = ind.get("bb_bandwidth", 0.04)
        if 0.02 <= bb_width <= 0.07:  # Healthy volatility range
            volat_score = 10.0
        elif bb_width < 0.02:
            volat_score = 6.0
        elif bb_width <= 0.10:
            volat_score = 4.0
        scores["volatility"] = volat_score

        total_score = sum(scores.values())
        total_score = round(min(100.0, max(0.0, total_score)), 1)

        # Category
        if total_score < 55.0:
            category = "NO_TRADE"
        elif total_score < 70.0:
            category = "WEAK"
        elif total_score < 85.0:
            category = "GOOD"
        else:
            category = "HIGH_QUALITY"

        return total_score, category, scores

    @classmethod
    def calculate_opportunity_score(
        cls,
        features: FeatureVector,
        regime_state: MarketRegimeState,
    ) -> Tuple[float, str]:
        """
        Calculates overall market attractiveness (Opportunity Score: 0 - 100).
        0-30: IGNORE
        30-50: WATCH
        50-70: CONSIDER
        70-85: TRADE
        85-100: HIGH_CONVICTION
        """
        ind = features.indicators
        adx = ind.get("adx", 15.0)
        vol_ratio = ind.get("volume_ratio", 1.0)
        bb_width = ind.get("bb_bandwidth", 0.04)
        has_div = (
            ind.get("bullish_divergence", 0.0) > 0.5 or ind.get("bearish_divergence", 0.0) > 0.5
        )

        score = 0.0
        # ADX clarity
        score += min(30.0, (adx / 40.0) * 30.0)
        # Volume presence
        score += min(25.0, (vol_ratio / 2.0) * 25.0)
        # Volatility health
        if 0.025 <= bb_width <= 0.08:
            score += 25.0
        else:
            score += 10.0
        # Divergence bonus
        if has_div:
            score += 20.0

        opp_score = round(min(100.0, max(0.0, score)), 1)
        if opp_score < 30.0:
            label = "IGNORE"
        elif opp_score < 50.0:
            label = "WATCH"
        elif opp_score < 70.0:
            label = "CONSIDER"
        elif opp_score < 85.0:
            label = "TRADE"
        else:
            label = "HIGH_CONVICTION"

        return opp_score, label
