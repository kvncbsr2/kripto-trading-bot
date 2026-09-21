"""Research candidate, not a validated profit model. No execution side effects."""

import numpy as np
import pandas as pd

from services.feature_engine.indicators.trend import calculate_ema
from services.feature_engine.volatility.volatility import calculate_atr
from services.strategy_engine.strategies.base_strategy import BaseStrategy
from shared.enums import MarketRegime, SignalDirection
from shared.schemas import Signal


class CostAwareTrendStrategy(BaseStrategy):
    """15m long-only breakout/reclaim; explicit reasons and cost-adjusted geometry.

    timestamp means candle OPEN time in UTC. Evaluation uses only completed bars.
    confidence is a heuristic, never an estimated win probability.
    Deliberately not registered/activated until independent validation.
    """

    def __init__(self, fee_rate=0.001, slippage_rate=0.0005, enabled=True):
        super().__init__("cost_aware_trend", enabled)
        if not all(np.isfinite(v) and 0 <= v < 0.05 for v in (fee_rate, slippage_rate)):
            raise ValueError("Invalid per-side cost assumptions")
        self.fee_rate = fee_rate
        self.slippage_rate = slippage_rate
        self.last_reason = "NOT_EVALUATED"

    def reject(self, reason):
        self.last_reason = reason
        return None

    def evaluate(self, features, regime_state):
        # A feature snapshot cannot establish the prior-bar reclaim or data continuity.
        return self.reject("CANDLE_HISTORY_REQUIRED")

    def evaluate_from_dataframe(self, df, symbol, *, as_of=None):
        if not self.enabled:
            return self.reject("DISABLED")
        cols = ["open", "high", "low", "close", "volume"]
        if df is None or not set(cols + ["timestamp"]).issubset(df.columns):
            return self.reject("MISSING_COLUMNS")
        try:
            now = pd.Timestamp.now(tz="UTC") if as_of is None else pd.Timestamp(as_of)
            if now.tzinfo is None:
                now = now.tz_localize("UTC")
            ts = pd.to_datetime(df.timestamp, utc=True, errors="raise")
            if ts.isna().any() or ts.duplicated().any() or not ts.is_monotonic_increasing:
                return self.reject("INVALID_TIMESTAMPS")
            closed = ts + pd.Timedelta(minutes=15) <= now
            bars = df.loc[closed, cols].astype(float).reset_index(drop=True)
            times = ts.loc[closed].reset_index(drop=True)
        except (ValueError, TypeError, OverflowError):
            return self.reject("INVALID_DATA")
        if len(bars) < 100:
            return self.reject("WARMUP_100_BARS")
        bars, times = bars.iloc[-100:], times.iloc[-100:]
        if not times.diff().iloc[1:].eq(pd.Timedelta(minutes=15)).all():
            return self.reject("CANDLE_GAP")
        if now - (times.iloc[-1] + pd.Timedelta(minutes=15)) >= pd.Timedelta(minutes=15):
            return self.reject("STALE_CANDLES")
        if not np.isfinite(bars.to_numpy()).all():
            return self.reject("NONFINITE_DATA")
        if ((bars[cols[:4]] <= 0).any().any() or (bars.volume < 0).any()
                or (bars.high < bars[["open", "close", "low"]].max(axis=1)).any()
                or (bars.low > bars[["open", "close", "high"]].min(axis=1)).any()):
            return self.reject("INVALID_OHLCV")

        close = bars.close
        fast, slow = calculate_ema(close, 20), calculate_ema(close, 50)
        atr = float(calculate_atr(bars.high, bars.low, close).iloc[-1])
        c, prev = bars.iloc[-1], bars.iloc[-2]
        if not (c.close > slow.iloc[-1] and fast.iloc[-1] > slow.iloc[-1]
                and slow.iloc[-1] > slow.iloc[-5]):
            return self.reject("NO_UPTREND")
        base_volume = float(bars.volume.iloc[-21:-1].mean())
        if base_volume <= 0 or c.volume < base_volume:
            return self.reject("LOW_VOLUME")
        if atr <= 0 or c.close - fast.iloc[-1] > 2 * atr:
            return self.reject("EXTENDED_PRICE")
        breakout = c.close > bars.high.iloc[-21:-1].max()
        reclaim = (prev.low <= fast.iloc[-2] and prev.close <= fast.iloc[-2]
                   and c.close > fast.iloc[-1] and c.close > prev.high)
        if not (breakout or reclaim):
            return self.reject("NO_BREAKOUT_OR_RECLAIM")

        # Do not tighten an invalidation stop to make a trade fit a risk limit.
        stop = min(float(bars.low.iloc[-5:].min()) - 0.2 * atr, c.close - 1.5 * atr)
        distance = c.close - stop
        if stop <= 0 or distance / c.close > 0.03:
            return self.reject("STOP_TOO_WIDE")
        target = c.close + 2.2 * distance
        entry_fill = c.close * (1 + self.slippage_rate)
        stop_fill = stop * (1 - self.slippage_rate)
        target_fill = target * (1 - self.slippage_rate)
        net_reward = target_fill * (1 - self.fee_rate) - entry_fill * (1 + self.fee_rate)
        net_risk = entry_fill * (1 + self.fee_rate) - stop_fill * (1 - self.fee_rate)
        if net_reward / net_risk < 1.4:
            return self.reject("INSUFFICIENT_NET_REWARD")
        self.last_reason = "BREAKOUT" if breakout else "RECLAIM"
        return Signal(
            symbol=symbol, timestamp=(times.iloc[-1] + pd.Timedelta(minutes=15)).to_pydatetime(),
            strategy=self.name, direction=SignalDirection.LONG,
            entry_price=float(c.close), stop_price=float(stop), take_profit=float(target),
            confidence=0.70, regime=MarketRegime.BULL_TREND,
            reason=self.last_reason,
            metadata={"net_rr": float(net_reward / net_risk), "signal_score": 70.0,
                      "fee_rate": self.fee_rate, "slippage_rate": self.slippage_rate,
                      "validation_status": "RESEARCH_ONLY"},
        )
