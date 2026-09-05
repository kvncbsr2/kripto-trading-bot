from datetime import datetime, timedelta, timezone

import pandas as pd

from services.backtest_engine.anti_lookahead import AntiLookaheadEngine
from services.strategy_engine.strategies.r10_rsi_divergence import R10RSIDivergenceStrategy


def test_r10_pivot_confirmation_lag():
    """
    Guarantees that a candidate pivot at index p is confirmed ONLY at p + right_bars.
    """
    strategy = R10RSIDivergenceStrategy(left_bars=5, right_bars=5)

    n_bars = 40
    base_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    dates = [base_time + timedelta(days=i) for i in range(n_bars)]

    # Create a clear swing low at index 10:
    # bars 5-9: decreasing, bar 10: lowest (100.0), bars 11-15: increasing
    prices = [150.0 - i * 2 for i in range(10)]  # 150 to 132
    prices.append(100.0)  # index 10: swing low
    prices.extend([100.0 + (i + 1) * 3 for i in range(29)])  # 103, 106, ...

    rsi_values = [40.0] * n_bars
    rsi_values[10] = 25.0  # low RSI

    df = pd.DataFrame({
        "timestamp": dates,
        "open": prices,
        "high": [p + 2.0 for p in prices],
        "low": [p - 2.0 for p in prices],
        "close": prices,
        "volume": [1000.0] * n_bars,
        "rsi": rsi_values,
        "atr": [5.0] * n_bars,
    })

    # At bar 12 (T=12), the pivot at 10 has only 2 right bars -> CANNOT be confirmed yet!
    low_pivots_12, _ = strategy.detect_pivots_strictly_causal(df, current_idx=12)
    assert len(low_pivots_12) == 0, "Pivot at index 10 must NOT be confirmed at bar 12"

    # At bar 14 (T=14), 4 right bars -> CANNOT be confirmed yet!
    low_pivots_14, _ = strategy.detect_pivots_strictly_causal(df, current_idx=14)
    assert len(low_pivots_14) == 0, "Pivot at index 10 must NOT be confirmed at bar 14"

    # At bar 15 (T=15, which is exactly 10 + right_bars 5): CONFIRMED!
    low_pivots_15, _ = strategy.detect_pivots_strictly_causal(df, current_idx=15)
    assert len(low_pivots_15) == 1, "Pivot at index 10 must be confirmed at bar 15"
    assert low_pivots_15[0].index == 10
    assert low_pivots_15[0].confirmation_index == 15


def test_anti_lookahead_engine_audit():
    audit_engine = AntiLookaheadEngine(right_bars_delay=5)

    t0 = datetime(2026, 1, 10, 0, 0, tzinfo=timezone.utc)
    t5 = datetime(2026, 1, 15, 0, 0, tzinfo=timezone.utc)

    # Valid causal signal: pivot at t0, signal emitted at t5
    audit_engine.record_signal_generation(
        signal_id="sig_01",
        data_available_until=t5,
        decision_timestamp=t5,
        execution_timestamp=t5,
        pivot_timestamp=t0,
    )

    report = audit_engine.run_audit()
    assert report.is_valid
    assert report.lookahead_violations_count == 0
