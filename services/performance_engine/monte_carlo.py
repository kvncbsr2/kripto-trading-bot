from typing import Any, Dict, List

import numpy as np


class MonteCarloSimulator:
    """
    Empirical Monte Carlo Simulation Engine for evaluating statistical robustness of trades.
    Uses bootstrap resampling with >= 5,000 iterations to measure:
    - Expected Drawdown, 95th & 99th percentile Drawdown, Worst-case Drawdown
    - Value at Risk (VaR 95%, VaR 99%) and Conditional VaR (CVaR / Expected Shortfall)
    - Risk of Ruin (probability equity falls below threshold)
    - Empirical Percentiles: Worst 5%, Median, Best 5%
    """

    @staticmethod
    def run_simulation(
        trade_pnls: List[float],
        initial_capital: float = 5000.0,
        iterations: int = 5000,
        ruin_threshold: float = 2500.0,
    ) -> Dict[str, Any]:
        # Ensure minimum 5000 iterations for statistical confidence
        iterations = max(5000, iterations)

        if len(trade_pnls) < 5:
            return {
                "iterations": iterations,
                "expected_drawdown": 0.0,
                "drawdown_95th": 0.0,
                "drawdown_99th": 0.0,
                "worst_case_drawdown": 0.0,
                "var_95": 0.0,
                "var_99": 0.0,
                "cvar_95": 0.0,
                "risk_of_ruin_pct": 0.0,
                "percentiles": {
                    "worst_5pct": initial_capital,
                    "median": initial_capital,
                    "best_5pct": initial_capital,
                },
                "avg_max_losing_streak": 0,
                "prob_losing_streak_3_plus": 0.0,
                "status": "INSUFFICIENT_TRADES_FOR_MONTE_CARLO",
            }

        pnls = np.array(trade_pnls)
        n_trades = len(pnls)

        sim_drawdowns = []
        sim_final_equities = []
        max_losing_streaks = []
        ruin_count = 0

        rng = np.random.default_rng(seed=42)

        for _ in range(iterations):
            # Resample trade order with replacement (empirical bootstrap)
            sampled_pnls = rng.choice(pnls, size=n_trades, replace=True)
            equity_path = initial_capital + np.cumsum(sampled_pnls)
            final_eq = float(equity_path[-1])
            sim_final_equities.append(final_eq)

            if np.any(equity_path <= ruin_threshold):
                ruin_count += 1

            # Drawdown calculation
            peaks = np.maximum.accumulate(equity_path)
            drawdowns = (peaks - equity_path) / np.maximum(peaks, 1.0)
            sim_drawdowns.append(float(np.max(drawdowns)))

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
        dd_99th = float(np.percentile(sim_drawdowns_arr, 99)) * 100.0
        worst_dd = float(np.max(sim_drawdowns_arr)) * 100.0

        # Returns distribution for VaR and CVaR
        sim_returns = (sim_final_equities_arr - initial_capital) / initial_capital
        var_95 = float(-np.percentile(sim_returns, 5)) * 100.0
        var_99 = float(-np.percentile(sim_returns, 1)) * 100.0
        cvar_tail = sim_returns[sim_returns <= np.percentile(sim_returns, 5)]
        cvar_95 = float(-np.mean(cvar_tail)) * 100.0 if len(cvar_tail) > 0 else var_95

        risk_of_ruin_pct = float(ruin_count / iterations) * 100.0
        worst_5pct_equity = float(np.percentile(sim_final_equities_arr, 5))
        median_equity = float(np.median(sim_final_equities_arr))
        best_5pct_equity = float(np.percentile(sim_final_equities_arr, 95))

        avg_max_streak = float(np.mean(max_losing_streaks))
        prob_streak_3_plus = float(np.mean(np.array(max_losing_streaks) >= 3)) * 100.0

        return {
            "iterations": iterations,
            "expected_drawdown": round(expected_dd, 2),
            "drawdown_95th": round(dd_95th, 2),
            "drawdown_99th": round(dd_99th, 2),
            "worst_case_drawdown": round(worst_dd, 2),
            "var_95": round(var_95, 2),
            "var_99": round(var_99, 2),
            "cvar_95": round(cvar_95, 2),
            "risk_of_ruin_pct": round(risk_of_ruin_pct, 2),
            "percentiles": {
                "worst_5pct": round(worst_5pct_equity, 2),
                "median": round(median_equity, 2),
                "best_5pct": round(best_5pct_equity, 2),
            },
            "avg_max_losing_streak": round(avg_max_streak, 1),
            "prob_losing_streak_3_plus": round(prob_streak_3_plus, 1),
            "median_final_equity": round(median_equity, 2),
            "status": "VALIDATED",
        }
