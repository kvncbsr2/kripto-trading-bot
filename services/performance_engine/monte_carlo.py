from typing import Any, Dict, List

import numpy as np


class MonteCarloSimulator:
    """
    Monte Carlo Simulation Engine for evaluating statistical robustness of trades.
    Identifies whether performance is likely skill or luck.
    """

    @staticmethod
    def run_simulation(
        trade_pnls: List[float],
        initial_capital: float = 5000.0,
        iterations: int = 1000,
    ) -> Dict[str, Any]:
        if len(trade_pnls) < 5:
            return {
                "iterations": iterations,
                "expected_drawdown": 0.0,
                "drawdown_95th": 0.0,
                "worst_case_drawdown": 0.0,
                "max_losing_streak": 0,
                "prob_losing_streak_3_plus": 0.0,
                "status": "INSUFFICIENT_TRADES_FOR_MONTE_CARLO",
            }

        pnls = np.array(trade_pnls)
        n_trades = len(pnls)

        sim_drawdowns = []
        sim_final_equities = []
        max_losing_streaks = []

        rng = np.random.default_rng(seed=42)

        for _ in range(iterations):
            # Resample trade order with replacement
            sampled_pnls = rng.choice(pnls, size=n_trades, replace=True)
            equity_path = initial_capital + np.cumsum(sampled_pnls)
            sim_final_equities.append(equity_path[-1])

            # Drawdown calculation
            peaks = np.maximum.accumulate(equity_path)
            drawdowns = (peaks - equity_path) / np.maximum(peaks, 1.0)
            sim_drawdowns.append(np.max(drawdowns))

            # Streak calculation
            is_loss = sampled_pnls < 0
            current_streak = 0
            max_streak = 0
            for loss in is_loss:
                if loss:
                    current_streak += 1
                    if current_streak > max_streak:
                        max_streak = current_streak
                else:
                    current_streak = 0
            max_losing_streaks.append(max_streak)

        sim_drawdowns_arr = np.array(sim_drawdowns)
        sim_final_equities_arr = np.array(sim_final_equities)

        expected_dd = float(np.mean(sim_drawdowns_arr)) * 100.0
        dd_95th = float(np.percentile(sim_drawdowns_arr, 95)) * 100.0
        worst_dd = float(np.max(sim_drawdowns_arr)) * 100.0
        avg_max_streak = float(np.mean(max_losing_streaks))
        prob_streak_3_plus = float(np.mean(np.array(max_losing_streaks) >= 3)) * 100.0

        return {
            "iterations": iterations,
            "expected_drawdown": round(expected_dd, 2),
            "drawdown_95th": round(dd_95th, 2),
            "worst_case_drawdown": round(worst_dd, 2),
            "avg_max_losing_streak": round(avg_max_streak, 1),
            "prob_losing_streak_3_plus": round(prob_streak_3_plus, 1),
            "median_final_equity": round(float(np.median(sim_final_equities_arr)), 2),
            "status": "VALIDATED",
        }
