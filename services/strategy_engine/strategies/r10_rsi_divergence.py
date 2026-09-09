from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

from services.feature_engine.indicators.momentum import calculate_rsi
from services.feature_engine.indicators.trend import calculate_ema
from services.feature_engine.volatility.volatility import calculate_atr
from services.strategy_engine.strategies.base_strategy import BaseStrategy
from shared.config import get_settings
from shared.enums import MarketRegime, SignalDirection
from shared.logging import get_logger
from shared.schemas import FeatureVector, MarketRegimeState, Signal

logger = get_logger("r10-rsi-divergence", service="strategy_engine")


@dataclass
class ConfirmedPivot:
    index: int
    timestamp: datetime
    price: float
    rsi: float
    is_low: bool
    confirmation_index: int
    confirmation_time: datetime


def _format_price_precision(price: float) -> float:
    if price <= 0:
        return 0.0
    if price < 0.0001:
        return round(price, 8)
    if price < 0.01:
        return round(price, 6)
    if price < 1.0:
        return round(price, 4)
    if price < 10.0:
        return round(price, 3)
    return round(price, 2)


class R10RSIDivergenceStrategy(BaseStrategy):
    """
    R10 RSI Divergence Swing Strategy with Strict Causal Anti-Lookahead Guarantee.
    Pivots at bar T are confirmed ONLY after right_bars (default 5) have elapsed.
    Signals are emitted strictly at the moment of confirmation (T + right_bars).
    """

    def __init__(
        self,
        name: str = "r10_rsi_divergence",
        rsi_length: int = 14,
        left_bars: int = 5,
        right_bars: int = 5,
        atr_multiplier: float = 1.5,
        risk_reward_ratio: float = 2.0,
        min_signal_score: float = 70.0,
        timeframe: str = "1d",
        confirmation_enabled: bool = True,
        enabled: bool = True,
    ):
        super().__init__(name=name, enabled=enabled)
        self.rsi_length = rsi_length
        self.left_bars = left_bars
        self.right_bars = right_bars
        self.atr_multiplier = atr_multiplier
        self.risk_reward_ratio = risk_reward_ratio
        self.min_signal_score = min_signal_score
        self.timeframe = timeframe
        # FIX (2026-09): R10_CONFIRMATION_ENABLED existed in config.py but was never
        # read anywhere in the codebase - a dead flag with no effect. External research
        # on RSI divergence trading (independent backtesting sources) consistently notes
        # that raw divergence signals are identified only in hindsight and that requiring
        # a structural confirmation (price reclaiming a short-term moving average, a
        # confirming candle, or a trendline break) is what separates reported win rates
        # in the ~55-65% range from the <40% range for unconfirmed divergence alone. This
        # wires the flag to an actual EMA-reclaim confirmation check below.
        self.confirmation_enabled = confirmation_enabled

    @staticmethod
    def calculate_signal_score_from_divergence(div_score: float) -> float:
        """Map divergence score 10..20 monotonically to 50..100."""
        return min(100.0, max(50.0, 50.0 + (div_score - 10.0) * 5.0))

    def detect_pivots_strictly_causal(
        self,
        df: pd.DataFrame,
        current_idx: int,
    ) -> Tuple[List[ConfirmedPivot], List[ConfirmedPivot]]:
        """
        Detects swing high and swing low pivots strictly causally up to current_idx.
        A candidate pivot at index p is confirmed if and only if p + right_bars <= current_idx.
        """
        low_pivots: List[ConfirmedPivot] = []
        high_pivots: List[ConfirmedPivot] = []

        if current_idx < (self.left_bars + self.right_bars):
            return low_pivots, high_pivots

        lows = df["low"].values
        highs = df["high"].values
        rsis = df["rsi"].values
        timestamps = df["timestamp"].values

        # Check candidate pivot positions p that could be confirmed by current_idx
        # p ranges from left_bars to (current_idx - right_bars)
        for p in range(self.left_bars, current_idx - self.right_bars + 1):
            # Swing Low check: lows[p] must be lowest in window and strictly below window mean
            window_low = lows[p - self.left_bars : p + self.right_bars + 1]
            if (
                lows[p] == np.min(window_low)
                and lows[p] < np.mean(window_low)
                and not np.isnan(rsis[p])
            ):
                low_pivots.append(
                    ConfirmedPivot(
                        index=p,
                        timestamp=pd.to_datetime(timestamps[p]).to_pydatetime(),
                        price=float(lows[p]),
                        rsi=float(rsis[p]),
                        is_low=True,
                        confirmation_index=p + self.right_bars,
                        confirmation_time=pd.to_datetime(
                            timestamps[p + self.right_bars]
                        ).to_pydatetime(),
                    )
                )

            # Swing High check: highs[p] must be highest in window and strictly above window mean
            window_high = highs[p - self.left_bars : p + self.right_bars + 1]
            if (
                highs[p] == np.max(window_high)
                and highs[p] > np.mean(window_high)
                and not np.isnan(rsis[p])
            ):
                high_pivots.append(
                    ConfirmedPivot(
                        index=p,
                        timestamp=pd.to_datetime(timestamps[p]).to_pydatetime(),
                        price=float(highs[p]),
                        rsi=float(rsis[p]),
                        is_low=False,
                        confirmation_index=p + self.right_bars,
                        confirmation_time=pd.to_datetime(
                            timestamps[p + self.right_bars]
                        ).to_pydatetime(),
                    )
                )

        return low_pivots, high_pivots

    def calculate_divergence_quality(
        self,
        pivot_1: ConfirmedPivot,
        pivot_2: ConfirmedPivot,
        current_atr: float,
        is_bullish: bool,
    ) -> float:
        """
        Calculates 0-100 Divergence Quality Score based on:
        - Pivot Separation (ideal: 8 to 40 bars)
        - RSI Difference Magnitude (ideal: >= 4.0 points)
        - Price Trend Clarity
        """
        score = 50.0
        separation = pivot_2.index - pivot_1.index

        # 1. Separation quality (ideal: between 8 and 35 bars)
        if 8 <= separation <= 35:
            score += 20.0
        elif separation < 5 or separation > 60:
            score -= 15.0

        # 2. RSI Delta magnitude
        rsi_delta = abs(pivot_2.rsi - pivot_1.rsi)
        if rsi_delta >= 5.0:
            score += 20.0
        elif rsi_delta >= 3.0:
            score += 10.0
        else:
            score -= 10.0

        # 3. Price Delta significance relative to ATR
        price_delta = abs(pivot_2.price - pivot_1.price)
        if current_atr > 0 and (price_delta / current_atr) >= 1.0:
            score += 10.0

        return max(0.0, min(100.0, score))

    def evaluate_from_dataframe(
        self,
        df: pd.DataFrame,
        symbol: str,
    ) -> Optional[Signal]:
        """
        Evaluates R10 divergence on a full historical or streaming dataframe.
        Guarantees that evaluation happens strictly on the last confirmed bar without lookahead.
        """
        if len(df) < (self.rsi_length + self.left_bars + self.right_bars + 5):
            return None

        # Ensure RSI, ATR, and EMA9 are computed
        if "rsi" not in df.columns:
            df["rsi"] = calculate_rsi(df["close"], self.rsi_length)
        if "atr" not in df.columns:
            df["atr"] = calculate_atr(df["high"], df["low"], df["close"], 14)
        if "ema_9" not in df.columns:
            df["ema_9"] = calculate_ema(df["close"], 9)

        current_idx = len(df) - 1
        current_candle = df.iloc[current_idx]
        current_price = float(current_candle["close"])
        current_atr = (
            float(current_candle["atr"])
            if not np.isnan(current_candle["atr"])
            else current_price * 0.02
        )
        current_time = pd.to_datetime(current_candle["timestamp"]).to_pydatetime()
        ema9_now = (
            float(current_candle["ema_9"])
            if not np.isnan(current_candle["ema_9"])
            else None
        )

        low_pivots, high_pivots = self.detect_pivots_strictly_causal(df, current_idx)

        # Check for Regular Bullish Divergence (Long)
        # Needs at least 2 confirmed low pivots, with the latest low pivot confirmed at current_idx
        if len(low_pivots) >= 2:
            p2 = low_pivots[-1]
            p1 = low_pivots[-2]

            # The pivot p2 must have just been confirmed at current_idx
            if p2.confirmation_index == current_idx:
                # Regular Bullish: Price Lower Low, RSI Higher Low
                if p2.price < p1.price and p2.rsi > p1.rsi and p2.rsi < 50.0:
                    quality = self.calculate_divergence_quality(
                        p1, p2, current_atr, is_bullish=True
                    )
                    signal_score = min(100.0, max(0.0, quality * 0.7 + 25.0))

                    if signal_score >= self.min_signal_score:
                        # Structural confirmation: price must have reclaimed the
                        # short-term EMA, not just show a raw RSI divergence.
                        bullish_confirmed = not (
                            self.confirmation_enabled and ema9_now is not None and current_price < ema9_now
                        )
                        stop_loss = _format_price_precision(p2.price - (current_atr * self.atr_multiplier))
                        risk_dist = current_price - stop_loss
                        if bullish_confirmed and risk_dist > 0:
                            take_profit = _format_price_precision(
                                current_price + (risk_dist * self.risk_reward_ratio)
                            )
                            return Signal(
                                symbol=symbol,
                                timestamp=current_time,
                                strategy="R10_RSI_DIVERGENCE",
                                direction=SignalDirection.LONG,
                                entry_price=current_price,
                                stop_price=stop_loss,
                                take_profit=take_profit,
                                confidence=round(signal_score / 100.0, 2),
                                regime=MarketRegime.BULL_TREND,
                                reason=f"R10 Bullish Divergence (P1={_format_price_precision(p1.price)} P2={_format_price_precision(p2.price)}, RSI1={p1.rsi:.1f} RSI2={p2.rsi:.1f})",
                                metadata={
                                    "strategy_family": "R10_DIVERGENCE",
                                    "divergence_type": "REGULAR_BULLISH",
                                    "pivot_1_time": p1.timestamp.isoformat(),
                                    "pivot_2_time": p2.timestamp.isoformat(),
                                    "confirmation_time": p2.confirmation_time.isoformat(),
                                    "divergence_quality": quality,
                                    "signal_score": signal_score,
                                    "is_real_time_safe": True,
                                    "timeframe": self.timeframe,
                                },
                            )

        # Check for Regular Bearish Divergence (Short)
        if len(high_pivots) >= 2:
            p2 = high_pivots[-1]
            p1 = high_pivots[-2]

            if p2.confirmation_index == current_idx:
                # Regular Bearish: Price Higher High, RSI Lower High
                if p2.price > p1.price and p2.rsi < p1.rsi and p2.rsi > 50.0:
                    quality = self.calculate_divergence_quality(
                        p1, p2, current_atr, is_bullish=False
                    )
                    signal_score = min(100.0, max(0.0, quality * 0.7 + 25.0))

                    if signal_score >= self.min_signal_score:
                        bearish_confirmed = not (
                            self.confirmation_enabled and ema9_now is not None and current_price > ema9_now
                        )
                        stop_loss = _format_price_precision(p2.price + (current_atr * self.atr_multiplier))
                        risk_dist = stop_loss - current_price
                        if bearish_confirmed and risk_dist > 0:
                            take_profit = _format_price_precision(
                                current_price - (risk_dist * self.risk_reward_ratio)
                            )
                            return Signal(
                                symbol=symbol,
                                timestamp=current_time,
                                strategy="R10_RSI_DIVERGENCE",
                                direction=SignalDirection.SHORT,
                                entry_price=current_price,
                                stop_price=stop_loss,
                                take_profit=take_profit,
                                confidence=round(signal_score / 100.0, 2),
                                regime=MarketRegime.BEAR_TREND,
                                reason=f"R10 Bearish Divergence (P1={_format_price_precision(p1.price)} P2={_format_price_precision(p2.price)}, RSI1={p1.rsi:.1f} RSI2={p2.rsi:.1f})",
                                metadata={
                                    "strategy_family": "R10_DIVERGENCE",
                                    "divergence_type": "REGULAR_BEARISH",
                                    "pivot_1_time": p1.timestamp.isoformat(),
                                    "pivot_2_time": p2.timestamp.isoformat(),
                                    "confirmation_time": p2.confirmation_time.isoformat(),
                                    "divergence_quality": quality,
                                    "signal_score": signal_score,
                                    "is_real_time_safe": True,
                                    "timeframe": self.timeframe,
                                },
                            )

        return None

    def evaluate(
        self,
        features: FeatureVector,
        regime_state: MarketRegimeState,
    ) -> Optional[Signal]:
        """
        BaseStrategy interface integration.
        Uses precomputed indicators in FeatureVector if divergence is confirmed.
        """
        if not self.enabled:
            return None

        ind = features.indicators
        close = ind.get("close")
        atr = ind.get("atr")
        if close is None or atr is None:
            return None

        is_bull_div = ind.get("bullish_divergence", 0.0) == 1.0
        is_bear_div = ind.get("bearish_divergence", 0.0) == 1.0
        div_score = ind.get("divergence_score", 0.0)

        if not (is_bull_div or is_bear_div):
            return None

        # Scale 10.0 - 20.0 divergence score range to 50.0 - 100.0
        signal_score = self.calculate_signal_score_from_divergence(div_score)
        if signal_score < self.min_signal_score:
            return None

        if is_bull_div:
            stop_loss = _format_price_precision(close - (atr * self.atr_multiplier))
            take_profit = _format_price_precision(close + ((close - stop_loss) * self.risk_reward_ratio))
            return Signal(
                symbol=features.symbol,
                timestamp=features.timestamp,
                strategy=self.name,
                direction=SignalDirection.LONG,
                entry_price=close,
                stop_price=stop_loss,
                take_profit=take_profit,
                confidence=round(signal_score / 100.0, 2),
                regime=regime_state.regime,
                reason="R10 Bullish Divergence with causal confirmation",
                metadata={
                    "divergence_score": div_score,
                    "signal_score": signal_score,
                    "is_real_time_safe": True,
                },
            )

        if is_bear_div:
            stop_loss = _format_price_precision(close + (atr * self.atr_multiplier))
            take_profit = _format_price_precision(close - ((stop_loss - close) * self.risk_reward_ratio))
            return Signal(
                symbol=features.symbol,
                timestamp=features.timestamp,
                strategy=self.name,
                direction=SignalDirection.SHORT,
                entry_price=close,
                stop_price=stop_loss,
                take_profit=take_profit,
                confidence=round(signal_score / 100.0, 2),
                regime=regime_state.regime,
                reason="R10 Bearish Divergence with causal confirmation",
                metadata={
                    "divergence_score": div_score,
                    "signal_score": signal_score,
                    "is_real_time_safe": True,
                },
            )

        return None


def create_r10_strategy_from_settings(settings: Optional[object] = None) -> R10RSIDivergenceStrategy:
    """Build the single canonical R10 strategy from application settings."""
    if settings is None:
        settings = get_settings()
    return R10RSIDivergenceStrategy(
        rsi_length=settings.R10_RSI_LENGTH,
        left_bars=settings.R10_PIVOT_LEFT,
        right_bars=settings.R10_PIVOT_RIGHT,
        atr_multiplier=settings.ATR_SL_MULTIPLIER,
        risk_reward_ratio=settings.PREFERRED_RISK_REWARD,
        min_signal_score=settings.MIN_SIGNAL_SCORE,
        timeframe=settings.R10_TIMEFRAME,
        confirmation_enabled=getattr(settings, "R10_CONFIRMATION_ENABLED", True),
        enabled=settings.R10_ENABLED,
    )
