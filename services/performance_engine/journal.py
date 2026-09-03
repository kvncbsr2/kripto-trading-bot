from datetime import datetime, timezone
from typing import Any, Dict, List

from services.performance_engine.metrics import PerformanceEngine
from services.performance_engine.monte_carlo import MonteCarloSimulator
from shared.schemas import Position


class ExperimentJournal:
    """
    Generates Daily Journals and 7-Day Final Reports with comprehensive
    sensitivity analysis, strategy breakdowns, and verdict recommendations.
    """

    @classmethod
    def generate_daily_journal(
        cls,
        day_number: int,
        starting_equity: float,
        ending_equity: float,
        day_positions: List[Position],
    ) -> Dict[str, Any]:
        metrics = PerformanceEngine.calculate_metrics(
            day_positions, initial_capital=starting_equity, current_equity=ending_equity
        )
        net_pnl = round(ending_equity - starting_equity, 2)
        target_hit_20 = net_pnl >= 20.0
        target_hit_50 = net_pnl >= 50.0
        target_hit_100 = net_pnl >= 100.0

        journal = {
            "day_number": day_number,
            "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "starting_equity": round(starting_equity, 2),
            "ending_equity": round(ending_equity, 2),
            "net_pnl": net_pnl,
            "trades_count": len(day_positions),
            "win_rate": metrics["win_rate"],
            "profit_factor": metrics["profit_factor"],
            "total_fees": metrics["total_fees"],
            "target_hit_20": target_hit_20,
            "target_hit_50": target_hit_50,
            "target_hit_100": target_hit_100,
            "risk_status": "NORMAL" if net_pnl > -50.0 else "DAILY_RISK_LOCK",
        }
        return journal

    @classmethod
    def generate_7day_final_report(
        cls,
        initial_capital: float,
        final_equity: float,
        all_closed_positions: List[Position],
        daily_journals: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        metrics = PerformanceEngine.calculate_metrics(
            all_closed_positions, initial_capital=initial_capital, current_equity=final_equity
        )

        pnls = [p.realized_pnl for p in all_closed_positions]
        mc_results = MonteCarloSimulator.run_simulation(pnls, initial_capital=initial_capital)

        # Target hits analysis
        hits_20 = sum(1 for d in daily_journals if d.get("target_hit_20"))
        hits_50 = sum(1 for d in daily_journals if d.get("target_hit_50"))
        hits_100 = sum(1 for d in daily_journals if d.get("target_hit_100"))

        # Strategy breakdown
        strategy_pnl: Dict[str, float] = {}
        coin_pnl: Dict[str, float] = {}
        for p in all_closed_positions:
            strat = p.strategy or "unknown"
            strategy_pnl[strat] = strategy_pnl.get(strat, 0.0) + p.realized_pnl
            coin_pnl[p.symbol] = coin_pnl.get(p.symbol, 0.0) + p.realized_pnl

        for k in strategy_pnl:
            strategy_pnl[k] = round(strategy_pnl[k], 2)
        for k in coin_pnl:
            coin_pnl[k] = round(coin_pnl[k], 2)

        best_strategy = max(strategy_pnl.items(), key=lambda x: x[1])[0] if strategy_pnl else "N/A"
        worst_strategy = min(strategy_pnl.items(), key=lambda x: x[1])[0] if strategy_pnl else "N/A"
        best_coin = max(coin_pnl.items(), key=lambda x: x[1])[0] if coin_pnl else "N/A"
        worst_coin = min(coin_pnl.items(), key=lambda x: x[1])[0] if coin_pnl else "N/A"

        # Sensitivity Analysis: +25% Fees, 2x Slippage
        original_fees = metrics["total_fees"]
        fee_stress_net_pnl = round(metrics["total_net_pnl"] - (original_fees * 0.25), 2)
        slippage_stress_net_pnl = round(metrics["total_net_pnl"] - (original_fees * 0.50), 2)

        # Verdict Decision
        if (
            metrics["total_net_pnl"] > 0
            and metrics["profit_factor"] >= 1.2
            and metrics["max_drawdown"] <= 5.0
            and fee_stress_net_pnl > 0
        ):
            verdict = "GREEN (PROMISING - Ready for extended 30-day paper test)"
        elif metrics["total_net_pnl"] >= 0:
            verdict = "YELLOW (INSUFFICIENT DATA / MARGINAL - Needs 30-day paper validation)"
        else:
            verdict = "RED (STRATEGY NOT VALIDATED - Do not use real money)"

        report = {
            "experiment_name": "7_DAY_5000_PAPER_TEST",
            "initial_capital": initial_capital,
            "final_equity": final_equity,
            "total_net_pnl": metrics["total_net_pnl"],
            "total_return_pct": metrics["total_return_pct"],
            "average_daily_pnl": round(metrics["total_net_pnl"] / max(1, len(daily_journals)), 2),
            "win_rate": metrics["win_rate"],
            "profit_factor": metrics["profit_factor"],
            "expectancy": metrics["expectancy"],
            "max_drawdown": metrics["max_drawdown"],
            "total_fees": metrics["total_fees"],
            "total_trades": metrics["total_trades"],
            "target_hits": {
                "target_20_hit_rate": f"{hits_20} / {len(daily_journals)} days",
                "target_50_hit_rate": f"{hits_50} / {len(daily_journals)} days",
                "target_100_hit_rate": f"{hits_100} / {len(daily_journals)} days",
            },
            "strategy_contributions": strategy_pnl,
            "coin_contributions": coin_pnl,
            "best_strategy": best_strategy,
            "worst_strategy": worst_strategy,
            "best_coin": best_coin,
            "worst_coin": worst_coin,
            "sensitivity_analysis": {
                "fee_plus_25pct_net_pnl": fee_stress_net_pnl,
                "slippage_2x_net_pnl": slippage_stress_net_pnl,
            },
            "monte_carlo": mc_results,
            "verdict": verdict,
            "is_paper_trading": True,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        return report
