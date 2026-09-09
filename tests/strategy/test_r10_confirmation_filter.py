"""
Regression test for wiring R10_CONFIRMATION_ENABLED to an actual EMA-reclaim
confirmation check (2026-09 fix).

Before this fix, R10_CONFIRMATION_ENABLED existed in shared/config/config.py but
was never read anywhere in the codebase - a dead flag. External research on RSI
divergence trading strategies consistently notes that a raw divergence signal is
only ever identified in hindsight, and that requiring the price to structurally
confirm the reversal (e.g. reclaiming a short-term moving average) is what
separates strategies reporting win rates in the ~55-65% range from the <40%
range reported for unconfirmed divergence alone.
"""

from datetime import datetime, timedelta, timezone

import pandas as pd

from services.strategy_engine.strategies.r10_rsi_divergence import R10RSIDivergenceStrategy


def _build_bullish_divergence_df(confirmation_close: float) -> pd.DataFrame:
    dates = [datetime(2025, 1, 1, tzinfo=timezone.utc) + timedelta(days=i) for i in range(40)]
    lows = [100.0] * 40
    highs = [105.0] * 40
    closes = [102.0] * 40
    rsis = [50.0] * 40

    # Pivot 1 at index 10: Price=85, RSI=24
    lows[10] = 85.0
    rsis[10] = 24.0
    # Pivot 2 at index 25: Price=80 (Lower Low), RSI=32 (Higher Low)
    lows[25] = 80.0
    rsis[25] = 32.0

    # Confirmation bar is index 30 (25 + right_bars 5).
    closes[30] = confirmation_close

    return pd.DataFrame(
        {"timestamp": dates, "low": lows, "high": highs, "close": closes, "rsi": rsis, "atr": [2.5] * 40}
    )


def test_confirmation_enabled_blocks_signal_below_ema():
    """Price still well below the short EMA at confirmation -> no signal."""
    strategy = R10RSIDivergenceStrategy(
        left_bars=5, right_bars=5, min_signal_score=60.0, confirmation_enabled=True
    )
    df = _build_bullish_divergence_df(confirmation_close=90.0)  # below the ~102 EMA
    signal = strategy.evaluate_from_dataframe(df.iloc[:31].copy(), symbol="BTC/USDT")
    assert signal is None, "Signal must be blocked when price hasn't reclaimed the short EMA"


def test_confirmation_disabled_allows_raw_divergence_signal():
    """Same unconfirmed price path, but with confirmation off -> fires as before."""
    strategy = R10RSIDivergenceStrategy(
        left_bars=5, right_bars=5, min_signal_score=60.0, confirmation_enabled=False
    )
    df = _build_bullish_divergence_df(confirmation_close=90.0)
    signal = strategy.evaluate_from_dataframe(df.iloc[:31].copy(), symbol="BTC/USDT")
    assert signal is not None, "With confirmation disabled, raw divergence should still fire"
