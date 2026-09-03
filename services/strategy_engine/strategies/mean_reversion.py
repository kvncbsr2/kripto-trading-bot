from typing import Optional

from services.strategy_engine.strategies.base_strategy import BaseStrategy
from shared.enums import MarketRegime, SignalDirection
from shared.schemas import FeatureVector, MarketRegimeState, Signal


class MeanReversionStrategy(BaseStrategy):
    """
    Strategy B: Mean Reversion Strategy
    Operates strictly in SIDEWAYS / RANGING market regimes.
    Vetoes all signals if market regime is HIGH_VOLATILITY or TRENDING.
    """

    def __init__(
        self,
        name: str = "mean_reversion",
        rsi_oversold: float = 32.0,
        rsi_overbought: float = 68.0,
        atr_multiplier: float = 1.5,
        risk_reward_ratio: float = 2.0,
        enabled: bool = True,
    ):
        super().__init__(name=name, enabled=enabled)
        self.rsi_oversold = rsi_oversold
        self.rsi_overbought = rsi_overbought
        self.atr_multiplier = atr_multiplier
        self.risk_reward_ratio = risk_reward_ratio

    def evaluate(
        self,
        features: FeatureVector,
        regime_state: MarketRegimeState,
    ) -> Optional[Signal]:
        if not self.enabled:
            return None

        # Absolute veto if regime is NOT SIDEWAYS or LOW_VOLATILITY
        if regime_state.regime not in [MarketRegime.SIDEWAYS, MarketRegime.LOW_VOLATILITY]:
            return None

        ind = features.indicators
        close = ind.get("close")
        bb_upper = ind.get("bb_upper")
        bb_middle = ind.get("bb_middle")
        bb_lower = ind.get("bb_lower")
        rsi = ind.get("rsi")
        atr = ind.get("atr")

        if (
            close is None
            or bb_upper is None
            or bb_middle is None
            or bb_lower is None
            or rsi is None
            or atr is None
        ):
            return None

        # Long Candidate: Price touching or below lower Bollinger Band and RSI oversold
        if close <= bb_lower * 1.002 and rsi <= self.rsi_oversold:
            stop_dist = atr * self.atr_multiplier
            stop_price = close - stop_dist
            # Target is the middle band (mean) or 1:2 R:R, whichever is higher
            take_profit = max(bb_middle, close + (stop_dist * self.risk_reward_ratio))

            return Signal(
                symbol=features.symbol,
                strategy=self.name,
                direction=SignalDirection.LONG,
                entry_price=close,
                stop_price=stop_price,
                take_profit=take_profit,
                confidence=0.75,
                regime=regime_state.regime,
                reason=f"Sideways mean-reversion bounce at lower BB ({close:.2f} <= {bb_lower:.2f}) with oversold RSI {rsi:.1f}",
            )

        # Short Candidate: Price touching or above upper Bollinger Band and RSI overbought
        if close >= bb_upper * 0.998 and rsi >= self.rsi_overbought:
            stop_dist = atr * self.atr_multiplier
            stop_price = close + stop_dist
            take_profit = min(bb_middle, close - (stop_dist * self.risk_reward_ratio))

            return Signal(
                symbol=features.symbol,
                strategy=self.name,
                direction=SignalDirection.SHORT,
                entry_price=close,
                stop_price=stop_price,
                take_profit=take_profit,
                confidence=0.75,
                regime=regime_state.regime,
                reason=f"Sideways mean-reversion rejection at upper BB ({close:.2f} >= {bb_upper:.2f}) with overbought RSI {rsi:.1f}",
            )

        return None
