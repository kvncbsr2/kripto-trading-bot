from typing import Optional

from services.strategy_engine.strategies.base_strategy import BaseStrategy
from shared.enums import MarketRegime, SignalDirection
from shared.schemas import FeatureVector, MarketRegimeState, Signal


class TrendFollowingStrategy(BaseStrategy):
    """
    Strategy A: Trend Following Strategy
    Long Logic: EMA20 > EMA50 > EMA200, ADX > 25, RSI in (45, 68), Bull Regime.
    Short Logic: EMA20 < EMA50 < EMA200, ADX > 25, RSI in (32, 55), Bear Regime.
    """

    def __init__(
        self,
        name: str = "trend_following",
        adx_threshold: float = 23.0,
        atr_multiplier: float = 2.0,
        risk_reward_ratio: float = 2.0,
        enabled: bool = True,
    ):
        super().__init__(name=name, enabled=enabled)
        self.adx_threshold = adx_threshold
        self.atr_multiplier = atr_multiplier
        self.risk_reward_ratio = risk_reward_ratio

    def evaluate(
        self,
        features: FeatureVector,
        regime_state: MarketRegimeState,
    ) -> Optional[Signal]:
        if not self.enabled:
            return None

        ind = features.indicators
        close = ind.get("close")
        ema_20 = ind.get("ema_20")
        ema_50 = ind.get("ema_50")
        ema_200 = ind.get("ema_200")
        adx = ind.get("adx")
        rsi = ind.get("rsi")
        atr = ind.get("atr")

        if (
            close is None
            or ema_20 is None
            or ema_50 is None
            or ema_200 is None
            or adx is None
            or rsi is None
            or atr is None
        ):
            return None

        # Long Candidate
        if (
            regime_state.regime == MarketRegime.BULL_TREND
            and ema_20 > ema_50 > ema_200
            and adx >= self.adx_threshold
            and 45.0 <= rsi <= 68.0
        ):
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
                confidence=min(1.0, 0.6 + (adx / 100.0)),
                regime=regime_state.regime,
                reason=f"Bullish EMA alignment (20>50>200) with healthy momentum RSI {rsi:.1f} and ADX {adx:.1f}",
            )

        # Short Candidate
        if (
            regime_state.regime == MarketRegime.BEAR_TREND
            and ema_20 < ema_50 < ema_200
            and adx >= self.adx_threshold
            and 32.0 <= rsi <= 55.0
        ):
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
                confidence=min(1.0, 0.6 + (adx / 100.0)),
                regime=regime_state.regime,
                reason=f"Bearish EMA alignment (20<50<200) with bearish momentum RSI {rsi:.1f} and ADX {adx:.1f}",
            )

        return None
