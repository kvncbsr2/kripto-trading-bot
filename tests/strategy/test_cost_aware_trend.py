import numpy as np
import pandas as pd
import pytest

from scripts.evaluate_cost_aware_trend import exit_fill, replay
from services.strategy_engine.strategies.cost_aware_trend import CostAwareTrendStrategy


def candles():
    close = 100 + np.arange(120) * 0.05
    df = pd.DataFrame(dict(timestamp=pd.date_range("2026-01-01", periods=120, freq="15min", tz="UTC"),
                           open=close - 0.05, high=close + 0.2, low=close - 0.6,
                           close=close, volume=np.full(120, 100.0)))
    df.loc[119, ["close", "high", "volume"]] = [106.4, 106.6, 150]
    return df


def evaluate(df, strategy=None):
    strategy = strategy or CostAwareTrendStrategy()
    return strategy.evaluate_from_dataframe(df, "TEST/USDT", as_of=df.timestamp.iloc[-1] + pd.Timedelta(minutes=15))


def test_breakout_is_valid_and_cost_adjusted():
    sig = evaluate(candles())
    assert sig is not None
    assert sig.stop_price < sig.entry_price < sig.take_profit
    assert sig.metadata["net_rr"] >= 1.4
    assert sig.reason == "BREAKOUT"


def test_future_and_open_candles_do_not_change_signal():
    df = candles()
    at = df.timestamp.iloc[-1] + pd.Timedelta(minutes=15)
    strategy = CostAwareTrendStrategy()
    before = strategy.evaluate_from_dataframe(df, "TEST", as_of=at)
    extra = df.iloc[-1:].copy()
    extra.timestamp = at
    extra[["open", "high", "low", "close"]] = [1000, 2000, 1, 1500]
    after = strategy.evaluate_from_dataframe(pd.concat([df, extra], ignore_index=True), "TEST", as_of=at)
    assert before is not None and after is not None
    assert before.model_dump() == after.model_dump()


@pytest.mark.parametrize("kind,reason", [("nan", "NONFINITE_DATA"), ("duplicate", "INVALID_TIMESTAMPS"),
                                         ("gap", "CANDLE_GAP"), ("volume", "LOW_VOLUME"),
                                         ("ohlc", "INVALID_OHLCV")])
def test_bad_data_and_missing_liquidity_rejected(kind, reason):
    df = candles()
    if kind == "nan":
        df.loc[119, "close"] = np.nan
    elif kind == "duplicate":
        df.loc[118, "timestamp"] = df.loc[117, "timestamp"]
    elif kind == "gap":
        df = df.drop(115)
    elif kind == "volume":
        df.loc[119, "volume"] = 1
    else:
        df.loc[119, "low"] = 200
    strategy = CostAwareTrendStrategy()
    assert evaluate(df, strategy) is None
    assert strategy.last_reason == reason


def test_costs_can_disqualify_signal():
    strategy = CostAwareTrendStrategy(fee_rate=0.01)
    assert evaluate(candles(), strategy) is None
    assert strategy.last_reason == "INSUFFICIENT_NET_REWARD"


def test_stale_data_rejected():
    df = candles()
    strategy = CostAwareTrendStrategy()
    assert strategy.evaluate_from_dataframe(df, "TEST", as_of=df.timestamp.iloc[-1] + pd.Timedelta(hours=1)) is None
    assert strategy.last_reason == "STALE_CANDLES"


def test_gap_and_ambiguous_bar_execution():
    price, reason = exit_fill(pd.Series(dict(open=90, low=89, high=120)), 95, 110, 0.001)
    assert reason == "STOP"
    assert price == pytest.approx(90 * 0.999)
    price, reason = exit_fill(pd.Series(dict(open=100, low=94, high=120)), 95, 110, 0)
    assert (price, reason) == (95, "STOP")


def test_replay_cash_ledger_reconciles_and_final_liquidation():
    df = candles()
    # Keep breakout open long enough to exercise terminal liquidation.
    df.loc[118, ["close", "high", "volume"]] = [106.3, 106.5, 150]
    result, curve, events = replay(df, "TEST", 1000)
    assert result["trades"]
    assert sum(t["net_pnl"] for t in result["trades"]) == pytest.approx(result["net_pnl"])
    assert sum(amount for _, amount in events) == pytest.approx(result["net_pnl"])
    assert curve[-1][1] == pytest.approx(1000 + result["net_pnl"])


def test_disabled():
    assert evaluate(candles(), CostAwareTrendStrategy(enabled=False)) is None
