from typing import Any, Dict, Optional

import numpy as np
import pandas as pd
import vectorbt as vbt

from shared.config import get_settings
from shared.logging import get_logger

logger = get_logger("vectorbt-engine", service="backtest_engine")
settings = get_settings()


class VectorBTBacktester:
    """
    Primary Quantitative Vectorized Backtesting Engine using VectorBT.
    Implements realistic fee modeling (0.1%), slippage (5 bps), and zero look-ahead bias.
    """

    def __init__(
        self,
        initial_capital: Optional[float] = None,
        fees: Optional[float] = None,
        slippage_bps: Optional[float] = None,
    ):
        cfg = get_settings()
        self.initial_capital = initial_capital if initial_capital is not None else getattr(cfg, "INITIAL_CAPITAL", 5000.0)
        self.fees = fees if fees is not None else getattr(cfg, "TAKER_FEE", 0.001)
        slip = slippage_bps if slippage_bps is not None else getattr(cfg, "SLIPPAGE_BPS", 5.0)
        self.slippage = slip / 10000.0

    def run_backtest_from_signals(
        self,
        close: pd.Series,
        entries: pd.Series,
        exits: pd.Series,
        short_entries: Optional[pd.Series] = None,
        short_exits: Optional[pd.Series] = None,
        timeframe: str = "15m",
    ) -> Dict[str, Any]:
        """
        Executes vectorized portfolio simulation with fees, slippage, and capital constraints.
        Dynamically maps timeframe to frequency string.
        """
        freq_map = {
            "1m": "1min",
            "3m": "3min",
            "5m": "5min",
            "15m": "15min",
            "30m": "30min",
            "1h": "1h",
            "2h": "2h",
            "4h": "4h",
            "6h": "6h",
            "8h": "8h",
            "12h": "12h",
            "1d": "1D",
            "3d": "3D",
            "1w": "1W",
        }
        mapped_freq = freq_map.get(timeframe.lower(), "15min")

        # Ensure indices match close.index to avoid VectorBT broadcast mismatch
        entries = pd.Series(entries.values, index=close.index)
        exits = pd.Series(exits.values, index=close.index)
        if short_entries is not None and isinstance(short_entries, pd.Series):
            short_entries = pd.Series(short_entries.values, index=close.index)
        if short_exits is not None and isinstance(short_exits, pd.Series):
            short_exits = pd.Series(short_exits.values, index=close.index)

        try:
            pf = vbt.Portfolio.from_signals(
                close=close,
                entries=entries,
                exits=exits,
                short_entries=short_entries if short_entries is not None else False,
                short_exits=short_exits if short_exits is not None else False,
                init_cash=self.initial_capital,
                fees=self.fees,
                slippage=self.slippage,
                freq=mapped_freq,
            )

            def _safe_metric(v, default=0.0):
                if v is None or pd.isna(v) or np.isinf(v):
                    return default
                return float(v)

            total_return_pct = _safe_metric(pf.total_return()) * 100.0
            final_value = _safe_metric(pf.final_value(), self.initial_capital)
            net_pnl = final_value - self.initial_capital
            max_dd_pct = _safe_metric(pf.max_drawdown()) * 100.0
            sharpe = _safe_metric(pf.sharpe_ratio())
            sortino = _safe_metric(pf.sortino_ratio())
            trades_count = int(pf.trades.count())
            win_rate = _safe_metric(pf.trades.win_rate()) * 100.0 if trades_count > 0 else 0.0
            pf_raw = pf.trades.profit_factor()
            if pd.isna(pf_raw) or np.isinf(pf_raw):
                profit_factor = 99.0 if (not pd.isna(pf_raw) and pf_raw > 0) else 0.0
            else:
                profit_factor = float(pf_raw)
            expectancy = (
                _safe_metric(pf.trades.expectancy())
                if trades_count > 0
                else 0.0
            )

            return {
                "engine": "vectorbt",
                "initial_capital": self.initial_capital,
                "final_equity": round(final_value, 2),
                "total_net_pnl": round(net_pnl, 2),
                "total_return_pct": round(total_return_pct, 2),
                "max_drawdown": round(max_dd_pct, 2),
                "sharpe_ratio": round(sharpe, 2),
                "sortino_ratio": round(sortino, 2),
                "total_trades": trades_count,
                "win_rate": round(win_rate, 2),
                "profit_factor": round(profit_factor, 2),
                "expectancy": round(expectancy, 2),
                "fees_modeled": self.fees,
                "slippage_modeled_bps": self.slippage * 10000.0,
            }
        except Exception as e:
            logger.error(f"VectorBT simulation error: {e}")
            raise
