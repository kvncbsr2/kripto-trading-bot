from typing import Any, Dict, Optional

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
        initial_capital: float = 5000.0,
        fees: float = 0.001,
        slippage_bps: float = 5.0,
    ):
        self.initial_capital = initial_capital
        self.fees = fees
        self.slippage = slippage_bps / 10000.0

    def run_backtest_from_signals(
        self,
        close: pd.Series,
        entries: pd.Series,
        exits: pd.Series,
        short_entries: Optional[pd.Series] = None,
        short_exits: Optional[pd.Series] = None,
    ) -> Dict[str, Any]:
        """
        Executes vectorized portfolio simulation with fees, slippage, and capital constraints.
        """
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
                freq="15m",
            )

            total_return_pct = float(pf.total_return()) * 100.0
            final_value = float(pf.final_value())
            net_pnl = final_value - self.initial_capital
            max_dd_pct = float(pf.max_drawdown()) * 100.0
            sharpe = float(pf.sharpe_ratio()) if not pd.isna(pf.sharpe_ratio()) else 0.0
            sortino = float(pf.sortino_ratio()) if not pd.isna(pf.sortino_ratio()) else 0.0
            trades_count = int(pf.trades.count())
            win_rate = float(pf.trades.win_rate()) * 100.0 if trades_count > 0 else 0.0
            profit_factor = (
                float(pf.trades.profit_factor()) if not pd.isna(pf.trades.profit_factor()) else 0.0
            )
            expectancy = float(pf.trades.expectancy()) if trades_count > 0 else 0.0

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
