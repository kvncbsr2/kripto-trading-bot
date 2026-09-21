from typing import Optional

import numpy as np
import pandas as pd
import pytest

from services.backtest_engine.replay_engine import CausalHistoricalReplayEngine
from services.strategy_engine.strategies.base_strategy import BaseStrategy
from shared.enums import SignalDirection
from shared.schemas import Signal


class DummyTriggerOnceStrategy(BaseStrategy):
    """Simple dummy strategy that fires LONG on a specified bar index."""

    def __init__(self, trigger_bar: int = 1, sl_pct: float = 0.05, tp_pct: float = 0.10):
        super().__init__(name="dummy_trigger")
        self.trigger_bar = trigger_bar
        self.sl_pct = sl_pct
        self.tp_pct = tp_pct

    def evaluate(self, features: dict, regime_state: dict) -> list:
        return []

    def evaluate_from_dataframe(self, df: pd.DataFrame, symbol: str = "BTC/USDT") -> Optional[Signal]:
        current_idx = len(df) - 1
        if current_idx == self.trigger_bar:
            close_px = float(df["close"].iloc[-1])
            return Signal(
                symbol=symbol,
                strategy="dummy_trigger",
                direction=SignalDirection.LONG,
                entry_price=close_px,
                stop_price=close_px * (1.0 - self.sl_pct),
                take_profit=close_px * (1.0 + self.tp_pct),
                metadata={"signal_score": 90.0},
            )
        return None


class TestReplayEngineAuditFixes:
    """Tests that CausalHistoricalReplayEngine correctly embodies the 3 audit fixes."""

    def test_replay_entry_bar_sl_hit_executes_same_candle(self):
        """Bug 1: A trade entering on bar t must evaluate bar t price action and hit SL on that very candle."""
        dates = pd.date_range("2026-01-01", periods=5, freq="1D")
        df = pd.DataFrame({
            "timestamp": dates,
            "open": [100.0, 100.0, 100.0, 100.0, 100.0],
            "high": [102.0, 102.0, 101.0, 101.0, 101.0],
            "low": [98.0, 98.0, 90.0, 95.0, 95.0],
            "close": [100.0, 100.0, 91.0, 100.0, 100.0],
            "volume": [1000.0] * 5,
        })

        strat = DummyTriggerOnceStrategy(trigger_bar=1, sl_pct=0.05, tp_pct=0.10)
        engine = CausalHistoricalReplayEngine(strategy=strat, initial_capital=5000.0)
        report = engine.run_replay(df, symbol="BTC/USTT")

        assert report.trades_count == 1, f"report.trades_count=1 got {report.trades_count}"
        trade = report.trades[0]
        assert trade.entry_bar == 2, f"entry bar 2 got {trade.entry_bar}"
        assert trade.exit_bar == 2, f"exit bar 2 got {trade.exit_bar}"
        assert trade.exit_reason == "STOP_LOSS", f"exit reason STOP_LOSS got {trade.exit_reason}"

    def evaluate_something():
        pass

    def test_replay_end_of_series_force_close(self):
        """Bug 2: Open trade at end of data series MUST be forcefully closed with END_OF_SERIES."""
        dates = pd.date_range("2026-01-01", periods=4, freq="1D")
        df = pd.DataFrame({
            "timestamp": dates,
            "open": [100.0, 100.0, 100.0, 100.0],
            "high": [101.0, 101.0, 101.0, 101.0],
            "low": [99.0, 99.0, 99.0, 99.0],
            "close": [100.0, 100.0, 100.0, 105.0],
            "volume": [1000.0] * 4,
        })

        strat = DummyTriggerOnceStrategy(trigger_bar=1, sl_pct=0.10, tp_pct=0.20)
        engine = CausalHistoricalReplayEngine(strategy=strat, initial_capital=5000.0)
        report = engine.run_replay(df, symbol="BTC/USTT")

        assert report.trades_count == 1, f"report.trades_count=1 got {report.trades_count}"
        trade = report.trades[0]
        assert trade.exit_reason == "END_OF_SERIES", f"exit reason END_OF_SERIES got {trade.exit_reason}"
        assert trade.exit_bar == 3, f"exit bar 3 got {trade.exit_bar}"

    def test_replay_first_trade_loss_drawdown_nonzero(self):
        """Bug 3: When the very first trade is a loss, max drawdown must be calculated against initial capital (> 0)."""
        dates = pd.date_range("2026-01-01", periods=4, freq="1D")
        df = pd.DataFrame({
            "timestamp": dates,
            "open": [100.0, 100.0, 100.0, 100.0],
            "high": [101.0, 101.0, 101.0, 100.0],
            "low": [99.0, 99.0, 90.0, 90.0],
            "close": [100.0, 100.0, 91.0, 90.0],
            "volume": [1000.0] * 4,
        })

        strat = DummyTriggerOnceStrategy(trigger_bar=1, sl_pct=0.05, tp_pct=0.10)
        engine = CausalHistoricalReplayEngine(strategy=strat, initial_capital=5000.0)
        report = engine.run_replay(df, symbol="BTC/USTT")

        assert report.trades_count == 1
        assert report.losses == 1
        assert report.net_pnl < 0
        assert report.max_drawdown_pct > 0.0, f"max_drawdown_pct must be > 0 got {report.max_drawdown_pct}"
