"""
Momentum & Dip Rebound Strategy (Real-Time Adaptive Quantitative Model)
=======================================================================
Calibrated to examine quantitative setup archetypes:
  - Momentum expansion after EMA9 reclaim with volume confirmation.
  - Deep oversold bounce with rejection wick & fast mean reversion.
  - Base support hold with RSI recovery and consolidation.
  - Trend continuation on moving average support.

Real-Time Adaptive Features:
  1. Dynamic ATR Volatility Profiling: Automatically adjusts stop distances and R:R targets.
  2. Volume Flow Expansion Detection: Rewards institutional volume surges (+5 to +10 pts).
  3. Real-Time Setup Classifier: Diagnoses market structure into the exact winning trade archetypes.
  4. Non-linear Dynamic Take-Profit: Scales up R:R (up to 2.5R - 2.8R) during high-volatility breakouts.
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

import numpy as np
import pandas as pd

from services.feature_engine.indicators.momentum import calculate_rsi
from services.feature_engine.indicators.trend import calculate_ema
from services.feature_engine.volatility.volatility import calculate_atr
from services.strategy_engine.strategies.base_strategy import BaseStrategy
from shared.enums import MarketRegime, SignalDirection
from shared.logging import add_system_log, get_logger
from shared.schemas import FeatureVector, MarketRegimeState, Signal

logger = get_logger("momentum-dip-rebound", service="strategy_engine")


def _format_price_precision(price: float) -> float:
    if price >= 100:
        return round(price, 2)
    elif price >= 1:
        return round(price, 4)
    elif price >= 0.01:
        return round(price, 6)
    else:
        return round(price, 8)


class MomentumDipReboundStrategy(BaseStrategy):
    """
    Real-Time Adaptive Momentum & Dip Rebound Strategy.
    Dynamically identifies winning setups (Breakout, Oversold Bounce, Base Hold, Trend Continuation)
    and calibrates execution parameters based on live volatility and volume flow.
    """

    def __init__(
        self,
        name: str = "momentum_dip_rebound",
        timeframe: str = "15m",
        min_signal_score: float = 65.0,
        risk_reward_ratio: float = 2.0,
        atr_multiplier: float = 1.5,
        max_risk_pct: float = 0.04,
        momentum_min_return: float = 0.6,
        oversold_threshold: float = 33.0,
        support_rsi_min: float = 33.0,
        support_rsi_max: float = 48.0,
        enabled: bool = True,
        **kwargs,
    ):
        super().__init__(name=name, enabled=enabled)
        self.timeframe = timeframe
        self.min_signal_score = min_signal_score
        self.risk_reward_ratio = risk_reward_ratio
        self.atr_multiplier = atr_multiplier
        self.max_risk_pct = max_risk_pct
        self.momentum_min_return = momentum_min_return
        self.oversold_threshold = oversold_threshold
        self.support_rsi_min = support_rsi_min
        self.support_rsi_max = support_rsi_max

    def evaluate_from_dataframe(
        self,
        df: pd.DataFrame,
        symbol: str,
    ) -> Optional[Signal]:
        """
        Direct DataFrame evaluation for AutonomousPaperTrader live scan loop.
        Evaluates closed candles strictly with zero lookahead.
        Dynamically adapts entry thresholds and targets based on real-time market behavior.
        """
        if not self.enabled or df is None or len(df) < 25:
            return None

        required_cols = {"open", "high", "low", "close"}
        if not required_cols.issubset(df.columns):
            return None

        close_series = df["close"].astype(float)
        high_series = df["high"].astype(float)
        low_series = df["low"].astype(float)
        open_series = df["open"].astype(float)

        rsi_series = calculate_rsi(close_series, 14)
        ema9_series = calculate_ema(close_series, 9)
        ema21_series = calculate_ema(close_series, 21)
        atr_series = calculate_atr(high_series, low_series, close_series, 14)

        # Real-time Volume Flow Metrics
        vol_series = df["volume"].astype(float) if "volume" in df.columns else pd.Series([1.0] * len(df))
        vol_ma20 = vol_series.rolling(20).mean().iloc[-1] if len(vol_series) >= 20 else vol_series.iloc[-1]
        curr_vol = float(vol_series.iloc[-1])
        vol_ratio = float(curr_vol / (vol_ma20 + 1e-9)) if vol_ma20 > 0 else 1.0

        # Latest closed bar indicators
        curr_close = float(close_series.iloc[-1])
        curr_open = float(open_series.iloc[-1])
        curr_high = float(high_series.iloc[-1])
        curr_low = float(low_series.iloc[-1])
        curr_rsi = float(rsi_series.iloc[-1])
        prev_rsi = float(rsi_series.iloc[-2]) if len(rsi_series) >= 2 else curr_rsi
        curr_ema9 = float(ema9_series.iloc[-1])
        curr_ema21 = float(ema21_series.iloc[-1])
        curr_atr = float(atr_series.iloc[-1])

        # Real-Time Volatility Regime
        atr_pct = (curr_atr / curr_close) * 100.0 if curr_close > 0 else 1.5
        volatility_regime = "HIGH_VOLATILITY" if atr_pct >= 2.0 else ("COMPRESSED" if atr_pct <= 0.8 else "NORMAL")

        # 3-candle momentum return (45m lookback on 15m timeframe)
        ref_idx = -4 if len(close_series) >= 4 else 0
        ref_close = float(close_series.iloc[ref_idx])
        ret_3c = ((curr_close - ref_close) / (ref_close + 1e-12)) * 100.0

        # Candle morphology (wick rejection analysis)
        candle_range = max(curr_high - curr_low, 1e-8)
        body_bottom = min(curr_open, curr_close)
        lower_shadow = max(body_bottom - curr_low, 0.0)
        lower_shadow_ratio = lower_shadow / candle_range

        factor_name: Optional[str] = None
        winning_model: str = ""
        confidence: float = 0.75
        signal_score: float = 65.0
        reason: str = ""
        dynamic_rr: float = self.risk_reward_ratio

        # =====================================================================
        # FACTOR 1: MOMENTUM BREAKOUT (NEAR Model - The Big Realized Winner)
        # =====================================================================
        if (
            curr_close >= curr_ema9
            and ret_3c >= self.momentum_min_return
            and (42.0 <= curr_rsi <= 70.0)
        ):
            factor_name = "MOMENTUM_BREAKOUT"
            winning_model = "NEAR_MOMENTUM_EXPANSION"
            confidence = 0.85
            base_score = 70.0 + (ret_3c * 4.0) + ((curr_rsi - 42.0) * 0.4)
            # Volume surge confirmation bonus (Institutional Flow)
            vol_bonus = min(10.0, (vol_ratio - 1.0) * 15.0) if vol_ratio > 1.0 else 0.0
            signal_score = min(96.0, base_score + vol_bonus)

            # In high-volatility momentum surges, allow targets to expand to 2.4R - 2.8R
            if atr_pct >= 1.8 and vol_ratio >= 1.2:
                dynamic_rr = min(2.8, round(self.risk_reward_ratio * 1.25, 2))

            reason = (
                f"🚀 NEAR Modeli (Momentum Kırılımı): EMA9 Reclaim ({curr_close:.4f} >= {curr_ema9:.4f}), "
                f"3c Getiri: +{ret_3c:.2f}%, RSI: {curr_rsi:.1f}, Hacim Çarpanı: {vol_ratio:.2f}x"
            )

        # =====================================================================
        # FACTOR 2: DEEP OVERSOLD BOUNCE (SUI Model - The Sharp V-Reversal)
        # =====================================================================
        elif (
            curr_rsi <= self.oversold_threshold
            and (
                (curr_close >= curr_open)
                or (lower_shadow_ratio >= 0.25)
                or (curr_rsi > prev_rsi)
            )
        ):
            factor_name = "DEEP_OVERSOLD_BOUNCE"
            winning_model = "SUI_OVERSOLD_V_REVERSAL"
            confidence = 0.82
            depth_bonus = (self.oversold_threshold - curr_rsi) * 1.6
            wick_bonus = lower_shadow_ratio * 12.0
            signal_score = min(94.0, 70.0 + depth_bonus + wick_bonus)
            dynamic_rr = max(2.0, self.risk_reward_ratio)

            reason = (
                f"⚡ SUI Modeli (Aşırı Satım V-Dönüşü): RSI: {curr_rsi:.1f} <= {self.oversold_threshold}, "
                f"Alt Fitil Reddi: %{lower_shadow_ratio * 100:.0f}, Dip Dönüş Teyitli"
            )

        # =====================================================================
        # FACTOR 3: SUPPORT BASE HOLD & REBOUND (SOL Model - Consolidation)
        # =====================================================================
        elif (
            (self.support_rsi_min < curr_rsi <= self.support_rsi_max)
            and (curr_rsi >= prev_rsi)
            and (curr_close >= (curr_ema9 * 0.9975))
        ):
            factor_name = "SUPPORT_HOLD_REBOUND"
            winning_model = "SOL_SUPPORT_ACCUMULATION"
            confidence = 0.78
            signal_score = min(90.0, 68.0 + ((curr_rsi - self.support_rsi_min) * 0.8))
            dynamic_rr = self.risk_reward_ratio

            reason = (
                f"🛡️ SOL Modeli (Destek Tabanı Tutunması): RSI Toparlanıyor ({curr_rsi:.1f}), "
                f"EMA9 Tabanı Korundu ({curr_close:.4f} >= {curr_ema9:.4f})"
            )

        # =====================================================================
        # FACTOR 4: TREND ANCHOR CONTINUATION (ETH & BTC Model)
        # =====================================================================
        elif (
            curr_close >= curr_ema9 >= curr_ema21
            and (45.0 <= curr_rsi <= 65.0)
            and (ret_3c >= 0.2)
        ):
            factor_name = "TREND_CONTINUATION"
            winning_model = "ETH_BTC_TREND_ANCHOR"
            confidence = 0.80
            signal_score = min(90.0, 72.0 + (ret_3c * 3.0))
            dynamic_rr = self.risk_reward_ratio

            reason = (
                f"📈 ETH/BTC Modeli (Trend İçi Devam): EMA9/21 Boğa Dizilimi, "
                f"RSI: {curr_rsi:.1f}, Pozitif İvme: +{ret_3c:.2f}%"
            )

        # No setup matched
        if not factor_name:
            return None

        # Filter by configured minimum score threshold
        if signal_score < self.min_signal_score:
            return None

        # Adaptive Risk Management: Dynamic ATR Stop & Dynamic R:R Take Profit
        if factor_name == "DEEP_OVERSOLD_BOUNCE":
            # Tighter stop just below the rejection candle low
            sl_dist_atr = max(curr_atr * 1.2, (curr_close - curr_low) + (curr_atr * 0.15))
        else:
            sl_dist_atr = max(curr_atr * self.atr_multiplier, curr_close * 0.005)

        # Enforce maximum single-trade risk ceiling (e.g. 4%)
        max_allowed_dist = curr_close * self.max_risk_pct
        stop_dist = min(sl_dist_atr, max_allowed_dist)

        stop_price = _format_price_precision(curr_close - stop_dist)
        take_profit = _format_price_precision(curr_close + (stop_dist * dynamic_rr))

        # Safe UTC Timestamp
        ts = datetime.now(timezone.utc)
        if "timestamp" in df.columns:
            last_ts = df["timestamp"].iloc[-1]
            if isinstance(last_ts, datetime):
                ts = last_ts
            elif isinstance(last_ts, str):
                try:
                    ts = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
                except Exception:
                    pass

        return Signal(
            symbol=symbol,
            timestamp=ts,
            strategy=self.name,
            direction=SignalDirection.LONG,
            entry_price=_format_price_precision(curr_close),
            stop_price=stop_price,
            take_profit=take_profit,
            confidence=round(confidence, 2),
            regime=MarketRegime.BULL_TREND if factor_name in {"MOMENTUM_BREAKOUT", "TREND_CONTINUATION"} else MarketRegime.SIDEWAYS,
            reason=reason,
            metadata={
                "strategy_family": "MOMENTUM_DIP_REBOUND",
                "factor": factor_name,
                "winning_model": winning_model,
                "signal_score": round(signal_score, 1),
                "score": round(signal_score, 1),
                "rsi": round(curr_rsi, 2),
                "ema9": round(curr_ema9, 4),
                "ema21": round(curr_ema21, 4),
                "atr": round(curr_atr, 4),
                "atr_pct": round(atr_pct, 2),
                "vol_ratio": round(vol_ratio, 2),
                "volatility_regime": volatility_regime,
                "ret_3c_pct": round(ret_3c, 2),
                "dynamic_rr": round(dynamic_rr, 2),
                "risk_reward_ratio": round(dynamic_rr, 2),
                "timeframe": self.timeframe,
            },
        )

    def evaluate(
        self,
        features: FeatureVector,
        regime_state: MarketRegimeState,
    ) -> Optional[Signal]:
        """
        BaseStrategy interface implementation for stream/feature evaluation.
        """
        if not self.enabled:
            return None

        ind = features.indicators
        close = ind.get("close")
        if close is None:
            return None

        rsi = ind.get("rsi", 50.0)
        ema9 = ind.get("ema_9") or ind.get("ema9") or close
        ema21 = ind.get("ema_21") or ind.get("ema21") or close
        atr = ind.get("atr") or (close * 0.015)
        ret_3c = ind.get("return_3c") or ind.get("return_3c_pct") or 0.0
        vol_ratio = ind.get("volume_ratio", 1.0)

        factor_name: Optional[str] = None
        winning_model: str = ""
        confidence: float = 0.75
        signal_score: float = 65.0
        reason: str = ""
        dynamic_rr: float = self.risk_reward_ratio

        if close >= ema9 and ret_3c >= self.momentum_min_return and (42.0 <= rsi <= 70.0):
            factor_name = "MOMENTUM_BREAKOUT"
            winning_model = "NEAR_MOMENTUM_EXPANSION"
            confidence = 0.85
            signal_score = min(96.0, 70.0 + (ret_3c * 4.0) + (min(vol_ratio - 1.0, 2.0) * 10.0 if vol_ratio > 1.0 else 0.0))
            if vol_ratio >= 1.2:
                dynamic_rr = min(2.8, round(self.risk_reward_ratio * 1.25, 2))
            reason = f"🚀 NEAR Modeli (Momentum Kırılımı): Close >= EMA9, 3c Ret: +{ret_3c:.2f}%"
        elif rsi <= self.oversold_threshold:
            factor_name = "DEEP_OVERSOLD_BOUNCE"
            winning_model = "SUI_OVERSOLD_V_REVERSAL"
            confidence = 0.82
            signal_score = min(94.0, 70.0 + ((self.oversold_threshold - rsi) * 1.6))
            reason = f"⚡ SUI Modeli (Aşırı Satım V-Dönüşü): RSI: {rsi:.1f} <= {self.oversold_threshold}"
        elif (self.support_rsi_min < rsi <= self.support_rsi_max) and (close >= ema9 * 0.9975):
            factor_name = "SUPPORT_HOLD_REBOUND"
            winning_model = "SOL_SUPPORT_ACCUMULATION"
            confidence = 0.78
            signal_score = min(90.0, 68.0 + ((rsi - self.support_rsi_min) * 0.8))
            reason = f"🛡️ SOL Modeli (Destek Tabanı): RSI: {rsi:.1f}, EMA9 Taban Tutunması"
        elif close >= ema9 >= ema21 and (45.0 <= rsi <= 65.0) and ret_3c >= 0.2:
            factor_name = "TREND_CONTINUATION"
            winning_model = "ETH_BTC_TREND_ANCHOR"
            confidence = 0.80
            signal_score = min(90.0, 72.0 + (ret_3c * 3.0))
            reason = f"📈 ETH/BTC Modeli (Trend İçi Devam): EMA Boğa Dizilimi"

        if not factor_name or signal_score < self.min_signal_score:
            return None

        stop_dist = min(atr * self.atr_multiplier, close * self.max_risk_pct)
        stop_price = _format_price_precision(close - stop_dist)
        take_profit = _format_price_precision(close + (stop_dist * dynamic_rr))

        return Signal(
            symbol=features.symbol,
            timestamp=features.timestamp,
            strategy=self.name,
            direction=SignalDirection.LONG,
            entry_price=_format_price_precision(close),
            stop_price=stop_price,
            take_profit=take_profit,
            confidence=round(confidence, 2),
            regime=regime_state.regime,
            reason=reason,
            metadata={
                "strategy_family": "MOMENTUM_DIP_REBOUND",
                "factor": factor_name,
                "winning_model": winning_model,
                "signal_score": round(signal_score, 1),
                "score": round(signal_score, 1),
                "rsi": round(rsi, 2),
                "ema9": round(ema9, 4),
                "atr": round(atr, 4),
                "vol_ratio": round(vol_ratio, 2),
                "dynamic_rr": round(dynamic_rr, 2),
                "risk_reward_ratio": round(dynamic_rr, 2),
                "timeframe": self.timeframe,
            },
        )
