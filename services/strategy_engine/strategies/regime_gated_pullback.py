from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd

from services.feature_engine.indicators.market_structure import analyze_market_structure
from services.feature_engine.indicators.momentum import calculate_macd, calculate_rsi
from services.feature_engine.indicators.trend import calculate_adx, calculate_ema
from services.feature_engine.volatility.volatility import calculate_atr
from services.feature_engine.volume.volume import calculate_volume_ratio, calculate_vwap
from services.strategy_engine.strategies.base_strategy import BaseStrategy
from shared.enums import MarketRegime, SignalDirection, Timeframe
from shared.logging import get_logger
from shared.schemas import Candle, FeatureVector, MarketRegimeState, Signal

logger = get_logger("regime-gated-pullback", service="strategy_engine")


def _format_price_precision(price: float) -> float:
    if price >= 100:
        return round(price, 2)
    elif price >= 1:
        return round(price, 4)
    elif price >= 0.01:
        return round(price, 6)
    else:
        return round(price, 8)


class RegimeGatedPullbackStrategy(BaseStrategy):
    """
    REGIME-GATED PULLBACK CONTINUATION STRATEGY
    
    1H Trend Filter (Bullish Alignment):
      - EMA20 > EMA50 > EMA200
      - ADX >= adx_threshold (default 25.0)
      - price > EMA50
      - Spot only: NO SHORTS.
      
    15M Pullback Entry (Reclaim Confirmation):
      - Price pulls back toward EMA20 or VWAP (touches or approaches within threshold)
      - RSI between rsi_min and rsi_max (default 45.0 - 60.0)
      - MACD histogram turns upward (hist[t] > hist[t-1])
      - volume_ratio >= volume_threshold (default 1.1)
      - Bullish market structure (Higher Low / Higher High)
      - 15m candle closes back above pullback/reclaim level (close > EMA20 or close > VWAP)
      
    Stop Loss:
      - Confirmed pullback swing low or ATR multiple
      
    Exit Models:
      - A: fixed 1.5R
      - B: fixed 2.0R
      - C: partial 1.0R + trailing remainder
      - D: ATR trailing
    """

    def __init__(
        self,
        name: str = "regime_gated_pullback",
        adx_threshold: float = 25.0,
        rsi_min: float = 45.0,
        rsi_max: float = 60.0,
        volume_threshold: float = 1.1,
        atr_multiplier: float = 1.5,
        risk_reward_ratio: float = 2.0,
        exit_model: str = "B", # "A": 1.5R, "B": 2R, "C": partial 1R + trailing, "D": ATR trailing
        pullback_band_pct: float = 0.0075, # 0.75% near EMA20 or VWAP
        enabled: bool = True,
    ):
        super().__init__(name=name, enabled=enabled)
        self.adx_threshold = adx_threshold
        self.rsi_min = rsi_min
        self.rsi_max = rsi_max
        self.volume_threshold = volume_threshold
        self.atr_multiplier = atr_multiplier
        self.risk_reward_ratio = risk_reward_ratio
        self.exit_model = exit_model
        self.pullback_band_pct = pullback_band_pct

    def evaluate_1h_trend(self, df_1h: pd.DataFrame) -> Tuple[bool, str, Dict[str, float]]:
        """
        1H Trend Filter:
        LONG only when:
        EMA20 > EMA50 > EMA200
        ADX >= threshold
        price > EMA50
        """
        if len(df_1h) < 200:
            return False, "INSUFFICIENT_1H_DATA", {}

        close = df_1h["close"]
        high = df_1h["high"]
        low = df_1h["low"]

        ema_20 = calculate_ema(close, 20).iloc[-1]
        ema_50 = calculate_ema(close, 50).iloc[-1]
        ema_200 = calculate_ema(close, 200).iloc[-1]
        adx = calculate_adx(high, low, close, 14).iloc[-1]
        last_close = close.iloc[-1]

        metrics = {
            "close_1h": float(last_close),
            "ema_20_1h": float(ema_20),
            "ema_50_1h": float(ema_50),
            "ema_200_1h": float(ema_200),
            "adx_1h": float(adx),
        }

        if np.isnan(ema_20) or np.isnan(ema_50) or np.isnan(ema_200) or np.isnan(adx):
            return False, "NAN_INDICATORS", metrics

        aligned = ema_20 > ema_50 > ema_200
        above_ema50 = last_close > ema_50
        adx_valid = adx >= self.adx_threshold

        if aligned and above_ema50 and adx_valid:
            return True, "1H_BULLISH_TREND_CONFIRMED", metrics
        return False, f"FILTER_FAILED (aligned={aligned}, price>ema50={above_ema50}, adx={adx:.1f}>={self.adx_threshold})", metrics

    def evaluate_15m_pullback(
        self,
        df_15m: pd.DataFrame,
        symbol: str,
        trend_1h_active: bool = True,
    ) -> Optional[Signal]:
        """
        Evaluates 15m closed candles for Pullback Continuation Entry.
        """
        if len(df_15m) < 60:
            return None

        close = df_15m["close"].values
        high = df_15m["high"].values
        low = df_15m["low"].values
        volume = df_15m["volume"].values
        timestamps = df_15m["timestamp"].values

        c_close = float(close[-1])
        c_high = float(high[-1])
        c_low = float(low[-1])
        c_time = pd.to_datetime(timestamps[-1]).to_pydatetime()

        # Indicators
        ema_20 = calculate_ema(pd.Series(close), 20).values
        ema_50 = calculate_ema(pd.Series(close), 50).values
        rsi = calculate_rsi(pd.Series(close), 14).values
        atr = calculate_atr(pd.Series(high), pd.Series(low), pd.Series(close), 14).values
        vwap = calculate_vwap(pd.Series(high), pd.Series(low), pd.Series(close), pd.Series(volume)).values
        vol_ratio = calculate_volume_ratio(pd.Series(volume), 20).values
        _, _, macd_hist = calculate_macd(pd.Series(close), 12, 26, 9)
        macd_hist = macd_hist.values

        curr_ema20 = ema_20[-1]
        curr_vwap = vwap[-1]
        curr_rsi = rsi[-1]
        curr_atr = atr[-1]
        curr_vol_ratio = vol_ratio[-1]
        curr_hist = macd_hist[-1]
        prev_hist = macd_hist[-2]

        if np.isnan(curr_ema20) or np.isnan(curr_vwap) or np.isnan(curr_rsi) or np.isnan(curr_atr):
            return None

        # 1. 1H Trend Requirement
        if not trend_1h_active:
            return None

        # 2. Pullback towards EMA20 or VWAP
        # Candle low pulled back near or slightly below EMA20 or VWAP
        dist_to_ema20 = abs(c_low - curr_ema20) / curr_ema20
        dist_to_vwap = abs(c_low - curr_vwap) / curr_vwap
        touched_or_near_pullback = (dist_to_ema20 <= self.pullback_band_pct) or (dist_to_vwap <= self.pullback_band_pct) or (c_low <= curr_ema20 and c_close >= curr_ema20)

        if not touched_or_near_pullback:
            return None

        # 3. RSI between 45 and 60
        if not (self.rsi_min <= curr_rsi <= self.rsi_max):
            return None

        # 4. MACD histogram turns upward
        if not (curr_hist > prev_hist):
            return None

        # 5. Volume ratio >= 1.1
        if curr_vol_ratio < self.volume_threshold:
            return None

        # 6. Reclaim: 15m candle closes back above EMA20 and above low of pullback
        reclaim_confirmed = c_close >= curr_ema20 and c_close > close[-2]
        if not reclaim_confirmed:
            return None

        # 7. Stop Geometry: confirmed swing low of pullback or ATR multiple
        recent_lows = low[-5:]
        swing_low = float(np.min(recent_lows))
        atr_stop = _format_price_precision(c_close - (curr_atr * self.atr_multiplier))
        # Use tighter of swing low (with small buffer) or ATR stop
        stop_price = max(_format_price_precision(swing_low * 0.999), atr_stop)
        
        # Ensure stop is below entry
        if stop_price >= c_close:
            stop_price = _format_price_precision(c_close - (curr_atr * self.atr_multiplier))

        risk_dist = c_close - stop_price
        if risk_dist <= 0:
            return None

        # Target based on selected exit model
        if self.exit_model == "A":
            take_profit = _format_price_precision(c_close + (risk_dist * 1.5))
            exit_notes = "Fixed 1.5R"
        elif self.exit_model == "B":
            take_profit = _format_price_precision(c_close + (risk_dist * self.risk_reward_ratio))
            exit_notes = "Fixed 2.0R"
        elif self.exit_model == "C":
            take_profit = _format_price_precision(c_close + (risk_dist * 1.0))
            exit_notes = "Partial 1.0R + Trailing"
        elif self.exit_model == "D":
            take_profit = _format_price_precision(c_close + (risk_dist * 2.5))
            exit_notes = "ATR Trailing Target"
        else:
            take_profit = _format_price_precision(c_close + (risk_dist * 2.0))
            exit_notes = "Default 2.0R"

        confidence_score = min(95.0, 70.0 + (curr_vol_ratio - 1.0) * 15.0 + (curr_rsi - 45.0) * 0.5)

        return Signal(
            symbol=symbol,
            timestamp=c_time,
            strategy=self.name,
            direction=SignalDirection.LONG,
            entry_price=c_close,
            stop_price=stop_price,
            take_profit=take_profit,
            confidence=round(confidence_score / 100.0, 2),
            regime=MarketRegime.BULL_TREND,
            reason=f"Regime-Gated Pullback Reclaim (RSI={curr_rsi:.1f}, VolRatio={curr_vol_ratio:.2f}, Model={self.exit_model})",
            metadata={
                "strategy_family": "PULLBACK_CONTINUATION",
                "exit_model": self.exit_model,
                "exit_notes": exit_notes,
                "rsi_15m": round(float(curr_rsi), 2),
                "vol_ratio": round(float(curr_vol_ratio), 2),
                "ema_20": round(float(curr_ema20), 2),
                "vwap": round(float(curr_vwap), 2),
                "atr": round(float(curr_atr), 2),
                "risk_dist": round(float(risk_dist), 2),
            },
        )

    def evaluate_from_dataframe(
        self,
        df: pd.DataFrame,
        symbol: str,
    ) -> Optional[Signal]:
        """
        Direct DataFrame evaluation for AutonomousPaperTrader live scan loop.
        Evaluates pullback continuation pattern on closed candles.
        Trend confirmation is derived from EMA20 >= EMA50 alignment on the dataframe.
        """
        if not self.enabled or len(df) < 50:
            return None

        # Check trend structure: EMA20 >= EMA50 (within 0.5% tolerance)
        close = df["close"]
        ema_20 = calculate_ema(close, 20).iloc[-1]
        ema_50 = calculate_ema(close, 50).iloc[-1]
        trend_1h_active = not np.isnan(ema_20) and not np.isnan(ema_50) and ema_20 >= (ema_50 * 0.995)

        return self.evaluate_15m_pullback(df_15m=df, symbol=symbol, trend_1h_active=trend_1h_active)

    def evaluate(
        self,
        features: FeatureVector,
        regime_state: MarketRegimeState,
    ) -> Optional[Signal]:
        """BaseStrategy interface implementation."""
        if not self.enabled:
            return None

        ind = features.indicators
        close = ind.get("close")
        ema_20 = ind.get("ema_20")
        ema_50 = ind.get("ema_50")
        ema_200 = ind.get("ema_200")
        rsi = ind.get("rsi")
        atr = ind.get("atr")
        vwap = ind.get("vwap")
        vol_ratio = ind.get("volume_ratio", 1.0)
        adx = ind.get("adx", 20.0)
        macd_hist = ind.get("macd_histogram", 0.0)

        if close is None or ema_20 is None or ema_50 is None or rsi is None or atr is None:
            return None

        # Filter: Long candidate only in Bull Trend
        if regime_state.regime != MarketRegime.BULL_TREND:
            return None

        # EMA alignment
        if ema_200 is not None and not (ema_20 > ema_50 > ema_200):
            return None

        # Pullback band
        ref_level = vwap if vwap is not None else ema_20
        dist = abs(close - ref_level) / ref_level
        if dist > self.pullback_band_pct:
            return None

        # RSI filter
        if not (self.rsi_min <= rsi <= self.rsi_max):
            return None

        # Volume ratio
        if vol_ratio < self.volume_threshold:
            return None

        stop_dist = atr * self.atr_multiplier
        stop_price = _format_price_precision(close - stop_dist)
        take_profit = _format_price_precision(close + (stop_dist * self.risk_reward_ratio))

        return Signal(
            symbol=features.symbol,
            timestamp=features.timestamp,
            strategy=self.name,
            direction=SignalDirection.LONG,
            entry_price=close,
            stop_price=stop_price,
            take_profit=take_profit,
            confidence=0.80,
            regime=MarketRegime.BULL_TREND,
            reason=f"Regime-Gated Pullback Continuation (RSI={rsi:.1f}, VolRatio={vol_ratio:.2f})",
            metadata={"strategy_family": "PULLBACK_CONTINUATION", "exit_model": self.exit_model},
        )
