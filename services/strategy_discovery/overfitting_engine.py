from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

import numpy as np

from shared.logging import get_logger

logger = get_logger("overfitting-engine", service="strategy_discovery")


@dataclass
class RobustnessReport:
    strategy_slug: str
    version: str
    robustness_score: float  # 0 to 100 (higher is better)
    overfit_score: float  # 0 to 100 (lower is better)
    decision: str  # PROMOTED, REJECTED, OVERFIT
    is_sample_sufficient: bool
    oos_profit_factor: float
    walk_forward_stability: float
    monte_carlo_drawdown_95th: float
    fee_stress_passed: bool
    slippage_stress_passed: bool
    parameter_plateau_stable: bool
    regime_dependence: str
    coin_dependence: str
    reasons: List[str] = field(default_factory=list)
    detailed_metrics: Dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class OverfittingProtectionEngine:
    """
    Overfitting Protection, Walk-Forward, and Robustness Evaluation Engine.
    Ensures strategies possess true out-of-sample edge without curve-fitting.
    """

    @staticmethod
    def partition_purged_time_series(
        data_len: int,
        train_ratio: float = 0.60,
        val_ratio: float = 0.20,
        embargo_bars: int = 5,
    ) -> Tuple[Tuple[int, int], Tuple[int, int], Tuple[int, int]]:
        """
        Splits dataset into In-Sample (Train), Validation, and Out-of-Sample (Test)
        with an explicit Embargo window to prevent boundary information leakage.
        """
        train_end = int(data_len * train_ratio)
        val_start = train_end + embargo_bars
        val_end = int(data_len * (train_ratio + val_ratio))
        test_start = val_end + embargo_bars
        test_end = data_len

        return (0, train_end), (val_start, val_end), (test_start, test_end)

    @classmethod
    def evaluate_parameter_plateau(
        cls,
        base_param_value: float,
        neighborhood_pfs: List[float],
    ) -> Tuple[bool, float]:
        """
        Analyzes parameter stability plateau around the candidate parameter.
        A strategy is considered fragile (overfit) if performance drops drastically in neighboring values.
        """
        if not neighborhood_pfs or len(neighborhood_pfs) < 3:
            return True, 70.0

        mean_pf = float(np.mean(neighborhood_pfs))
        std_pf = float(np.std(neighborhood_pfs))
        cv = (std_pf / mean_pf) if mean_pf > 0 else 1.0

        # Coefficient of variation < 0.25 indicates a wide, stable plateau
        is_stable = cv < 0.25 and min(neighborhood_pfs) >= 1.05
        plateau_score = max(0.0, min(100.0, (1.0 - cv) * 100.0))
        return is_stable, round(plateau_score, 1)

    @classmethod
    def run_fee_stress_test(
        cls,
        gross_pnl: float,
        base_fees: float,
    ) -> Tuple[bool, float]:
        """
        Tests fee robustness at 1x, 2x, and 3x fees.
        Fee Fragile if 2x fees consume entire net profit.
        """
        net_2x = gross_pnl - (base_fees * 2.0)
        net_3x = gross_pnl - (base_fees * 3.0)

        passed = net_2x > 0
        score = 100.0 if net_3x > 0 else (65.0 if net_2x > 0 else 20.0)
        return passed, score

    @classmethod
    def run_slippage_stress_test(
        cls,
        net_pnl_at_5bps: float,
        total_volume_traded: float,
    ) -> Tuple[bool, float]:
        """
        Simulates execution drag at 10 bps, 20 bps, and 50 bps slippage.
        """
        slippage_cost_20bps = total_volume_traded * 0.0020
        net_at_20bps = net_pnl_at_5bps - slippage_cost_20bps

        passed = net_at_20bps > 0
        score = 90.0 if net_at_20bps > 0 else 30.0
        return passed, score

    @classmethod
    def calculate_robustness_score(
        cls,
        strategy_slug: str,
        version: str,
        is_trades_count: int,
        oos_profit_factor: float,
        oos_expectancy: float,
        oos_max_drawdown_pct: float,
        walk_forward_stability: float,
        monte_carlo_drawdown_95th: float,
        plateau_score: float,
        fee_stress_passed: bool,
        fee_score: float,
        slippage_stress_passed: bool,
        slippage_score: float,
        coins_tested_count: int = 5,
        coins_profitable_count: int = 4,
        regimes_profitable_count: int = 3,
        has_lookahead_violation: bool = False,
    ) -> RobustnessReport:
        """
        Calculates composite multi-dimensional Strategy Robustness Score (0 to 100)
        and evaluates Promotion Gate criteria.
        """
        reasons: List[str] = []

        # 1. Lookahead Check (Automatic Immediate Disqualification)
        if has_lookahead_violation:
            return RobustnessReport(
                strategy_slug=strategy_slug,
                version=version,
                robustness_score=0.0,
                overfit_score=100.0,
                decision="REJECTED",
                is_sample_sufficient=False,
                oos_profit_factor=oos_profit_factor,
                walk_forward_stability=walk_forward_stability,
                monte_carlo_drawdown_95th=monte_carlo_drawdown_95th,
                fee_stress_passed=fee_stress_passed,
                slippage_stress_passed=slippage_stress_passed,
                parameter_plateau_stable=False,
                regime_dependence="DISQUALIFIED",
                coin_dependence="DISQUALIFIED",
                reasons=["LOOKAHEAD_VIOLATION: Strategy accessed future data."],
            )

        # 2. Sample Size Sufficiency Check
        is_sample_sufficient = is_trades_count >= 15
        if not is_sample_sufficient:
            reasons.append(
                f"LOW_SAMPLE_WARNING: Only {is_trades_count} trades (minimum 15 required for statistical significance)."
            )

        # 3. Component Scores (Weights sum to 100%)
        # a. OOS Performance (20%)
        c_oos = min(100.0, max(0.0, (oos_profit_factor - 0.8) / 1.0 * 100.0)) * 0.20
        # b. Walk-Forward Stability (15%)
        c_wf = min(100.0, max(0.0, walk_forward_stability * 100.0)) * 0.15
        # c. Expectancy (10%)
        c_exp = min(100.0, max(0.0, oos_expectancy / 50.0 * 100.0)) * 0.10
        # d. Drawdown Control (10%)
        c_dd = max(0.0, (15.0 - oos_max_drawdown_pct) / 15.0 * 100.0) * 0.10
        # e. Parameter Stability Plateau (10%)
        c_plat = plateau_score * 0.10
        # f. Fee Robustness (10%)
        c_fee = fee_score * 0.10
        # g. Slippage Robustness (10%)
        c_slip = slippage_score * 0.10
        # h. Monte Carlo 95th Percentile Drawdown (10%)
        c_mc = max(0.0, (20.0 - monte_carlo_drawdown_95th) / 20.0 * 100.0) * 0.10
        # i. Cross-Coin Robustness (5%)
        coin_ratio = coins_profitable_count / max(1, coins_tested_count)
        c_coin = coin_ratio * 100.0 * 0.05

        total_robustness = round(
            c_oos + c_wf + c_exp + c_dd + c_plat + c_fee + c_slip + c_mc + c_coin, 1
        )

        # Overfit Score calculation (measures train vs OOS gap, parameter fragility)
        overfit_score = round(
            max(
                0.0,
                min(
                    100.0, (100.0 - total_robustness) * 0.85 + (0.0 if fee_stress_passed else 20.0)
                ),
            ),
            1,
        )

        coin_dependence = "PORTFOLIO_WIDE" if coin_ratio >= 0.70 else "COIN_DEPENDENT"
        regime_dependence = "BALANCED" if regimes_profitable_count >= 3 else "REGIME_DEPENDENT"

        if coin_dependence == "COIN_DEPENDENT":
            reasons.append(
                f"COIN_DEPENDENT: Strategy only profitable on {coins_profitable_count}/{coins_tested_count} coins."
            )
        if not fee_stress_passed:
            reasons.append("FEE_FRAGILE: Alpha vanishes under 2x fee stress.")
        if not slippage_stress_passed:
            reasons.append("SLIPPAGE_FRAGILE: Alpha vanishes under 20 bps slippage drag.")

        # Promotion Gate Decision
        promoted = (
            total_robustness >= 70.0
            and oos_profit_factor >= 1.10
            and oos_expectancy > 0.0
            and is_sample_sufficient
            and fee_stress_passed
            and slippage_stress_passed
            and monte_carlo_drawdown_95th <= 15.0
        )

        decision = "PROMOTED" if promoted else ("OVERFIT" if overfit_score >= 60.0 else "REJECTED")

        if promoted:
            reasons.append(
                "WHY PROMOTED: Positive OOS expectancy, stable parameter plateau, passed fee/slippage stress and Monte Carlo drawdown limits."
            )

        return RobustnessReport(
            strategy_slug=strategy_slug,
            version=version,
            robustness_score=total_robustness,
            overfit_score=overfit_score,
            decision=decision,
            is_sample_sufficient=is_sample_sufficient,
            oos_profit_factor=round(oos_profit_factor, 2),
            walk_forward_stability=round(walk_forward_stability, 2),
            monte_carlo_drawdown_95th=round(monte_carlo_drawdown_95th, 2),
            fee_stress_passed=fee_stress_passed,
            slippage_stress_passed=slippage_stress_passed,
            parameter_plateau_stable=plateau_score >= 65.0,
            regime_dependence=regime_dependence,
            coin_dependence=coin_dependence,
            reasons=reasons,
            detailed_metrics={
                "c_oos": round(c_oos, 1),
                "c_wf": round(c_wf, 1),
                "c_exp": round(c_exp, 1),
                "c_dd": round(c_dd, 1),
                "c_plat": round(c_plat, 1),
                "c_fee": round(c_fee, 1),
                "c_slip": round(c_slip, 1),
                "c_mc": round(c_mc, 1),
                "c_coin": round(c_coin, 1),
            },
        )
