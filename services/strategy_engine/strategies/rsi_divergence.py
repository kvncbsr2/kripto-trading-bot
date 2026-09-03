from typing import Optional

from services.signal_engine.scorer import SignalScorer
from services.strategy_engine.strategies.base_strategy import BaseStrategy
from shared.enums import MarketRegime, SignalDirection
from shared.schemas import FeatureVector, MarketRegimeState, Signal


class RSIDivergenceStrategy(BaseStrategy):
    """
    Strategy C: RSI Divergence Swing Strategy
    Produces high-quality swing candidates based on confirmed RSI divergences.
    Requires confluence of divergence + structure/volume confirmation.
    """

    def __init__(
        self,
        name: str = "rsi_divergence",
        atr_multiplier: float = 1.5,
        risk_reward_ratio: float = 2.0,
        min_score: float = 55.0,
        enabled: bool = True,
    ):
        super().__init__(name=name, enabled=enabled)
        self.atr_multiplier = atr_multiplier
        self.risk_reward_ratio = risk_reward_ratio
        self.min_score = min_score

    def evaluate(
        self,
        features: FeatureVector,
        regime_state: MarketRegimeState,
    ) -> Optional[Signal]:
        if not self.enabled:
            return None

        # Absolute veto if regime is extreme volatility
        if regime_state.regime == MarketRegime.HIGH_VOLATILITY:
            return None

        ind = features.indicators
        close = ind.get("close")
        atr = ind.get("atr")
        bull_div = ind.get("bullish_divergence", 0.0) > 0.5
        bear_div = ind.get("bearish_divergence", 0.0) > 0.5

        if not close or not atr or (not bull_div and not bear_div):
            return None

        # Long candidate on Bullish Divergence
        if bull_div:
            score, category, subscores = SignalScorer.score_signal(
                features, regime_state, SignalDirection.LONG, has_divergence=True
            )
            if score >= self.min_score:
                stop_dist = atr * self.atr_multiplier
                stop_price = close - stop_dist
                take_profit = close + (stop_dist * self.risk_reward_ratio)

                return Signal(
                    symbol=features.symbol,
                    strategy=self.name,
                    direction=SignalDirection.LONG,
                    entry_price=close,
                    stop_price=stop_price,
                    take_profit=take_profit,
                    confidence=round(score / 100.0, 2),
                    regime=regime_state.regime,
                    reason=f"Bullish RSI Divergence confirmed (Score: {score}/100 [{category}])",
                    metadata={
                        "score": score,
                        "category": category,
                        "subscores": subscores,
                    },
                )

        # Short candidate on Bearish Divergence
        if bear_div:
            score, category, subscores = SignalScorer.score_signal(
                features, regime_state, SignalDirection.SHORT, has_divergence=True
            )
            if score >= self.min_score:
                stop_dist = atr * self.atr_multiplier
                stop_price = close + stop_dist
                take_profit = close - (stop_dist * self.risk_reward_ratio)

                return Signal(
                    symbol=features.symbol,
                    strategy=self.name,
                    direction=SignalDirection.SHORT,
                    entry_price=close,
                    stop_price=stop_price,
                    take_profit=take_profit,
                    confidence=round(score / 100.0, 2),
                    regime=regime_state.regime,
                    reason=f"Bearish RSI Divergence confirmed (Score: {score}/100 [{category}])",
                    metadata={
                        "score": score,
                        "category": category,
                        "subscores": subscores,
                    },
                )

        return None
