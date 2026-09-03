from typing import Any, Dict, List

import numpy as np

from shared.schemas import Position


class PerformanceEngine:
    @staticmethod
    def calculate_metrics(
        closed_positions: List[Position],
        initial_capital: float = 5000.0,
        current_equity: float = 5000.0,
    ) -> Dict[str, Any]:
        total_trades = len(closed_positions)
        if total_trades == 0:
            return {
                "initial_capital": initial_capital,
                "current_equity": current_equity,
                "total_net_pnl": 0.0,
                "total_return_pct": 0.0,
                "gross_profit": 0.0,
                "gross_loss": 0.0,
                "total_fees": 0.0,
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "win_rate": 0.0,
                "profit_factor": 0.0,
                "expectancy": 0.0,
                "average_trade": 0.0,
                "average_win": 0.0,
                "average_loss": 0.0,
                "largest_win": 0.0,
                "largest_loss": 0.0,
                "sharpe_ratio": 0.0,
                "sortino_ratio": 0.0,
                "max_drawdown": 0.0,
            }

        pnls = [p.realized_pnl for p in closed_positions]
        fees = [p.fees_paid for p in closed_positions]

        total_net_pnl = sum(pnls)
        total_fees = sum(fees)
        total_return_pct = (total_net_pnl / initial_capital) * 100.0

        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        gross_profit = sum(wins)
        gross_loss = abs(sum(losses))

        winning_trades = len(wins)
        losing_trades = len(losses)
        win_rate = (winning_trades / total_trades) * 100.0 if total_trades > 0 else 0.0

        profit_factor = (
            round(gross_profit / gross_loss, 2)
            if gross_loss > 0
            else (99.0 if gross_profit > 0 else 0.0)
        )

        avg_win = np.mean(wins) if wins else 0.0
        avg_loss = np.mean(losses) if losses else 0.0
        avg_trade = np.mean(pnls)

        # Expectancy = (Win% * Avg Win) - (Loss% * Avg Loss)
        win_prob = winning_trades / total_trades
        loss_prob = losing_trades / total_trades
        expectancy = (win_prob * avg_win) + (loss_prob * avg_loss)

        largest_win = max(wins) if wins else 0.0
        largest_loss = min(losses) if losses else 0.0

        # Equity curve & Max Drawdown
        equity_curve = [initial_capital]
        for p in pnls:
            equity_curve.append(equity_curve[-1] + p)

        peak = initial_capital
        max_dd = 0.0
        for eq in equity_curve:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd

        # Sharpe & Sortino
        returns = np.array(pnls) / initial_capital
        std_returns = np.std(returns) if len(returns) > 1 else 1e-6
        sharpe = float((np.mean(returns) / (std_returns + 1e-9)) * np.sqrt(252))

        neg_returns = returns[returns < 0]
        downside_std = np.std(neg_returns) if len(neg_returns) > 1 else 1e-6
        sortino = float((np.mean(returns) / (downside_std + 1e-9)) * np.sqrt(252))

        return {
            "initial_capital": initial_capital,
            "current_equity": current_equity,
            "total_net_pnl": round(total_net_pnl, 2),
            "total_return_pct": round(total_return_pct, 2),
            "gross_profit": round(gross_profit, 2),
            "gross_loss": round(gross_loss, 2),
            "total_fees": round(total_fees, 2),
            "total_trades": total_trades,
            "winning_trades": winning_trades,
            "losing_trades": losing_trades,
            "win_rate": round(win_rate, 1),
            "profit_factor": profit_factor,
            "expectancy": round(expectancy, 2),
            "average_trade": round(avg_trade, 2),
            "average_win": round(avg_win, 2),
            "average_loss": round(avg_loss, 2),
            "largest_win": round(largest_win, 2),
            "largest_loss": round(largest_loss, 2),
            "sharpe_ratio": round(sharpe, 2),
            "sortino_ratio": round(sortino, 2),
            "max_drawdown": round(max_dd * 100.0, 2),
        }
