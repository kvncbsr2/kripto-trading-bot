"""
Turbo Fast-Strike Scalp Strategy (High-Velocity Shortcut Quantitative Engine)
=============================================================================
Designed specifically for high-velocity profit accumulation and rapid capital turnover:
  1. Targets high-beta liquid movers on 5m/1m timeframe.
  2. Aggressive quick-strike triggers:
     - FLASH_DIP_SNAPBACK: Extreme sub-30 RSI dip + immediate price wick rejection.
     - BREAKOUT_SURGE: Volume-backed EMA9 reclaim with accelerating 3-candle momentum.
  3. Micro-target capture (+1.2% to +2.5% TP) with tight asymmetric stop (0.8% to 1.2% SL).
  4. Minimizes market dwell time (rotates capital into next hot setup within 15-45 minutes).
"""

from datetime import datetime, timezone
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

from services.feature_engine.indicators.momentum import calculate_rsi
from services.feature_engine.indicators.trend import calculate_ema
from services.feature_engine.volatility.volatility import calculate_atr
from services.strategy_engine.strategies.base_strategy import BaseStrategy
from shared.enums import MarketRegime, SignalDirection
from shared.logging import get_logger
from shared.schemas import FeatureVector, MarketRegimeState, Signal

logger = get_logger("turbo-fast-strike", service="strategy_engine")

try:
    from services.strategy_engine.adaptive_learning import adaptive_learning_engine
except ImportError:
    adaptive_learning_engine = None


def _format_price_precision(price: float) -> float:
    if price >= 100:
        return round(price, 2)
    elif price >= 1:
        return round(price, 4)
    elif price >= 0.01:
        return round(price, 6)
    else:
        return round(price, 8)


class TurboFastStrikeStrategy(BaseStrategy):
    """
    Turbo Fast-Strike Scalp Strategy.
    High-frequency, high-conviction scalper designed to capture quick +1.5% to +2.5% pops
    with fast turnaround and capital rotation.
    """

    def __init__(
        self,
        name: str = "turbo_fast_strike",
        timeframe: str = "5m",
        min_signal_score: float = 60.0,
        risk_reward_ratio: float = 2.0,
        target_profit_pct: float = 0.018,  # 1.8% quick profit
        stop_loss_pct: float = 0.009,       # 0.9% tight stop
        min_volume_ratio: float = 1.2,      # 20% above 20-bar volume average
        enabled: bool = True,
        **kwargs,
    ):
        super().__init__(name=name, enabled=enabled)
        self.timeframe = timeframe
        self.min_signal_score = min_signal_score
        self.risk_reward_ratio = risk_reward_ratio
        self.target_profit_pct = target_profit_pct
        self.stop_loss_pct = stop_loss_pct
        self.min_volume_ratio = min_volume_ratio

    def evaluate_from_dataframe(
        self,
        df: pd.DataFrame,
        symbol: str,
    ) -> Optional[Signal]:
        """
        Direct DataFrame evaluation for high-velocity 5m/1m scalp scanning.
        Strictly causal on closed bars, zero lookahead.
        """
        if not self.enabled or df is None or len(df) < 20:
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

        # Volume Surge Calculation
        vol_series = df["volume"].astype(float) if "volume" in df.columns else pd.Series([1.0] * len(df))
        vol_ma20 = vol_series.rolling(20).mean().iloc[-1] if len(vol_series) >= 20 else vol_series.iloc[-1]
        curr_vol = float(vol_series.iloc[-1])
        vol_ratio = float(curr_vol / (vol_ma20 + 1e-9)) if vol_ma20 > 0 else 1.0

        curr_close = float(close_series.iloc[-1])
        curr_open = float(open_series.iloc[-1])
        curr_high = float(high_series.iloc[-1])
        curr_low = float(low_series.iloc[-1])
        curr_rsi = float(rsi_series.iloc[-1])
        prev_rsi = float(rsi_series.iloc[-2]) if len(rsi_series) >= 2 else curr_rsi
        curr_ema9 = float(ema9_series.iloc[-1])
        curr_ema21 = float(ema21_series.iloc[-1])
        curr_atr = float(atr_series.iloc[-1])

        # 3-bar return rate
        ref_idx = -4 if len(close_series) >= 4 else 0
        ref_close = float(close_series.iloc[ref_idx])
        ret_3c = ((curr_close - ref_close) / (ref_close + 1e-12)) * 100.0

        # Candle Anatomy & Wick Rejection Analysis
        candle_range = max(curr_high - curr_low, 1e-8)
        body_top = max(curr_open, curr_close)
        body_bottom = min(curr_open, curr_close)
        lower_shadow = max(body_bottom - curr_low, 0.0)
        upper_shadow = max(curr_high - body_top, 0.0)
        lower_shadow_ratio = lower_shadow / candle_range
        upper_shadow_ratio = upper_shadow / candle_range

        # Dynamically learned thresholds
        learned_atr_mult = adaptive_learning_engine.get_adapted_atr_multiplier() if adaptive_learning_engine else 1.20
        learned_rsi_cutoff = adaptive_learning_engine.get_adapted_rsi_cutoff() if adaptive_learning_engine else 32.0

        trigger_type: Optional[str] = None
        confidence: float = 0.80
        signal_score: float = 65.0
        reason: str = ""

        # ---------------------------------------------------------------------
        # SETUP 1: FLASH DIP REBOUND (Immediate snapback from oversold dump)
        # ---------------------------------------------------------------------
        if curr_rsi <= learned_rsi_cutoff and (lower_shadow_ratio >= 0.20 or curr_close > curr_open or curr_rsi > prev_rsi) and upper_shadow_ratio < 0.45:
            trigger_type = "FLASH_DIP_REBOUND"
            confidence = 0.86
            oversold_intensity = max(0.0, (learned_rsi_cutoff - curr_rsi) * 2.0)
            vol_boost = min(10.0, max(0.0, (vol_ratio - 1.0) * 15.0))
            signal_score = min(96.0, 72.0 + oversold_intensity + vol_boost)
            reason = (
                f"⚡ Turbo Dip Sıçraması: RSI={curr_rsi:.1f} (<={learned_rsi_cutoff:.1f} Eşik), "
                f"Alt Fitil=%{lower_shadow_ratio * 100:.0f}, Hacim={vol_ratio:.2f}x"
            )

        # ---------------------------------------------------------------------
        # SETUP 2: BREAKOUT ACCELERATION (Volume surge reclaiming EMA9 - Anti Bull-Trap Guard)
        # ---------------------------------------------------------------------
        elif (
            curr_close >= curr_ema9
            and ret_3c >= 0.4
            and (44.0 <= curr_rsi <= 68.0)
            and curr_close > curr_open
            and upper_shadow_ratio < 0.35  # KESİN RET: %35'ten büyük tepe fitili (bull trap / satış baskısı) varsa girme
            and vol_ratio >= 1.15          # KESİN RET: Hacim teyidi yoksa sığ tahtada sahte kırılıma girme
        ):
            trigger_type = "BREAKOUT_ACCELERATION"
            confidence = 0.84
            momentum_boost = min(12.0, ret_3c * 6.0)
            vol_boost = min(12.0, max(0.0, (vol_ratio - 1.0) * 15.0))
            signal_score = min(95.0, 70.0 + momentum_boost + vol_boost)
            reason = (
                f"🚀 Turbo Momentum Kırılımı: EMA9 Üzerinde ({curr_close:.4f}), "
                f"3-Bar İvme=+%{ret_3c:.2f}, RSI={curr_rsi:.1f}, Hacim={vol_ratio:.2f}x, TepeFitili=%{upper_shadow_ratio * 100:.0f}"
            )

        # ---------------------------------------------------------------------
        # SETUP 3: FAST PULLBACK RECLAIM (Shallow dip on strong trend)
        # ---------------------------------------------------------------------
        elif (
            curr_close >= curr_ema21
            and (curr_ema9 >= curr_ema21 * 0.998)
            and (38.0 <= curr_rsi <= 52.0)
            and (curr_rsi >= prev_rsi)
            and upper_shadow_ratio < 0.40
        ):
            trigger_type = "FAST_PULLBACK_RECLAIM"
            confidence = 0.80
            signal_score = min(90.0, 68.0 + (curr_rsi - 38.0) * 1.0)
            reason = (
                f"🎯 Turbo Trend Desteği: EMA21 Tabanı Tutundu, "
                f"RSI Dönüşü ({curr_rsi:.1f}), Hızlı Giriş"
            )

        if not trigger_type or signal_score < self.min_signal_score:
            return None

        # Asymmetric Micro Risk-Reward Modeling:
        # Stop Distance calibrated by learned dynamic ATR multiplier (Anti-Noise buffer)
        atr_stop_dist = max(curr_atr * learned_atr_mult, curr_close * 0.006)
        fixed_stop_dist = curr_close * self.stop_loss_pct
        stop_dist = min(atr_stop_dist, fixed_stop_dist)

        # Cost-aware Dynamic Take Profit: guaranteed to exceed Net R:R >= 1.6 after taker fees and slippage
        friction = 0.0015
        unit_risk = stop_dist + (curr_close + (curr_close - stop_dist)) * friction
        min_tp_required = (unit_risk * 1.65) + (curr_close * 2.0 * friction)
        tp_dist = max(stop_dist * self.risk_reward_ratio, curr_close * self.target_profit_pct, min_tp_required)

        stop_price = _format_price_precision(curr_close - stop_dist)
        take_profit = _format_price_precision(curr_close + tp_dist)

        ts = datetime.now(timezone.utc)
        if "timestamp" in df.columns:
            last_ts = df["timestamp"].iloc[-1]
            if isinstance(last_ts, str):
                try:
                    last_ts = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
                except Exception:
                    last_ts = None
            # Only override with last_ts if this is a historical backtest run (>2 hours in past)
            if isinstance(last_ts, datetime):
                tz_last = last_ts if last_ts.tzinfo else last_ts.replace(tzinfo=timezone.utc)
                if abs((datetime.now(timezone.utc) - tz_last).total_seconds()) > 7200:
                    ts = tz_last

        return Signal(
            symbol=symbol,
            timestamp=ts,
            strategy=self.name,
            direction=SignalDirection.LONG,
            entry_price=_format_price_precision(curr_close),
            stop_price=stop_price,
            take_profit=take_profit,
            confidence=round(confidence, 2),
            regime=MarketRegime.BULL_TREND if trigger_type == "BREAKOUT_ACCELERATION" else MarketRegime.SIDEWAYS,
            reason=reason,
            metadata={
                "strategy_family": "TURBO_FAST_STRIKE",
                "trigger_type": trigger_type,
                "signal_score": round(signal_score, 1),
                "score": round(signal_score, 1),
                "rsi": round(curr_rsi, 2),
                "ema9": round(curr_ema9, 4),
                "ema21": round(curr_ema21, 4),
                "atr": round(curr_atr, 4),
                "vol_ratio": round(vol_ratio, 2),
                "target_profit_pct": round((tp_dist / curr_close) * 100.0, 2),
                "risk_reward_ratio": round(tp_dist / (stop_dist + 1e-9), 2),
                "timeframe": self.timeframe,
            },
        )

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
        if close is None:
            return None

        rsi = ind.get("rsi", 50.0)
        ema9 = ind.get("ema_9") or ind.get("ema9") or close
        atr = ind.get("atr") or (close * 0.01)
        ret_3c = ind.get("return_3c") or 0.0
        vol_ratio = ind.get("volume_ratio", 1.0)

        trigger_type: Optional[str] = None
        confidence: float = 0.80
        signal_score: float = 65.0
        reason: str = ""

        if rsi <= 32.0:
            trigger_type = "FLASH_DIP_REBOUND"
            confidence = 0.86
            signal_score = min(96.0, 74.0 + (32.0 - rsi) * 2.0)
            reason = f"⚡ Turbo Dip Sıçraması (RSI: {rsi:.1f})"
        elif close >= ema9 and ret_3c >= 0.4:
            trigger_type = "BREAKOUT_ACCELERATION"
            confidence = 0.84
            signal_score = min(95.0, 72.0 + (ret_3c * 5.0))
            reason = f"🚀 Turbo Momentum Kırılımı (3c Ret: +{ret_3c:.2f}%)"

        if not trigger_type or signal_score < self.min_signal_score:
            return None

        stop_dist = min(atr * 1.2, close * self.stop_loss_pct)
        tp_dist = max(stop_dist * self.risk_reward_ratio, close * self.target_profit_pct)

        stop_price = _format_price_precision(close - stop_dist)
        take_profit = _format_price_precision(close + tp_dist)

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
                "strategy_family": "TURBO_FAST_STRIKE",
                "trigger_type": trigger_type,
                "signal_score": round(signal_score, 1),
                "score": round(signal_score, 1),
                "rsi": round(rsi, 2),
                "ema9": round(ema9, 4),
                "atr": round(atr, 4),
                "vol_ratio": round(vol_ratio, 2),
                "target_profit_pct": round((tp_dist / close) * 100.0, 2),
                "risk_reward_ratio": round(tp_dist / (stop_dist + 1e-9), 2),
                "timeframe": self.timeframe,
            },
        )
