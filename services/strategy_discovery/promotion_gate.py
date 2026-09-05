from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List

from services.strategy_discovery.overfitting_engine import RobustnessReport
from shared.logging import get_logger

logger = get_logger("promotion-gate", service="strategy_discovery")


@dataclass
class PromotionEvaluationResult:
    approved: bool
    status: str  # "APPROVED" or "REJECTED"
    strategy_slug: str
    version: str
    reasons: List[str] = field(default_factory=list)
    criteria_checks: Dict[str, bool] = field(default_factory=dict)
    evaluated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class PromotionGate:
    """
    Authoritative Strategy Promotion Gate (Section 94 / Requirement 21).
    Evaluates whether a backtested strategy variant qualifies for live or paper promotion.
    Mandatory criteria:
    1. Walk-forward stability >= 0.70
    2. Monte Carlo 95th drawdown <= 15.0%
    3. Fee stress test passed (net_pnl > 0 at 2x fees)
    4. Slippage stress test passed (net_pnl > 0 at 10 bps)
    5. Zero lookahead violations (has_lookahead_violation == False)
    6. Minimum 100 historical trades (is_trades_count >= 100 or total_trades >= 100)
    7. Minimum 3 distinct market regimes profitable (regimes_profitable_count >= 3)
    8. Robustness score >= 75.0
    If ANY single condition fails, status is REJECTED.
    """

    @staticmethod
    def evaluate(
        report: RobustnessReport,
        min_trades: int = 100,
        min_robustness_score: float = 75.0,
        min_wf_stability: float = 0.70,
        max_mc_drawdown_pct: float = 15.0,
        min_profitable_regimes: int = 3,
    ) -> PromotionEvaluationResult:
        reasons: List[str] = []
        checks: Dict[str, bool] = {}

        # 1. Walk-forward stability >= 0.70
        wf_pass = report.walk_forward_stability >= min_wf_stability
        checks["walk_forward_stability"] = wf_pass
        if not wf_pass:
            reasons.append(
                f"Walk-Forward Stability insufficient: {report.walk_forward_stability:.2f} < {min_wf_stability}"
            )

        # 2. Monte Carlo 95th Drawdown <= 15%
        mc_pass = report.monte_carlo_drawdown_95th <= max_mc_drawdown_pct
        checks["monte_carlo_drawdown_95th"] = mc_pass
        if not mc_pass:
            reasons.append(
                f"Monte Carlo 95th DD too high: {report.monte_carlo_drawdown_95th:.1f}% > {max_mc_drawdown_pct}%"
            )

        # 3. Fee stress test passed
        fee_pass = bool(report.fee_stress_passed)
        checks["fee_stress_passed"] = fee_pass
        if not fee_pass:
            reasons.append("Failed 2x fee stress test (Net PnL is negative under 2x fees)")

        # 4. Slippage stress test passed
        slip_pass = bool(report.slippage_stress_passed)
        checks["slippage_stress_passed"] = slip_pass
        if not slip_pass:
            reasons.append("Failed 10 bps slippage stress test (Net PnL is negative under 10 bps slippage)")

        # 5. Zero lookahead violations
        lookahead_pass = not report.has_lookahead_violation
        checks["zero_lookahead_violations"] = lookahead_pass
        if not lookahead_pass:
            reasons.append("Strict lookahead audit failed: future data leakage detected")

        # 6. Minimum 100 historical trades
        trades_pass = report.is_trades_count >= min_trades
        checks["min_trades_count"] = trades_pass
        if not trades_pass:
            reasons.append(f"Insufficient trade sample size: {report.is_trades_count} < {min_trades} trades")

        # 7. Minimum 3 distinct market regimes profitable
        regimes_pass = report.regimes_profitable_count >= min_profitable_regimes
        checks["min_profitable_regimes"] = regimes_pass
        if not regimes_pass:
            reasons.append(
                f"Lacks multi-regime profitability: {report.regimes_profitable_count} < {min_profitable_regimes} regimes"
            )

        # 8. Robustness score >= 75.0
        score_pass = report.robustness_score >= min_robustness_score
        checks["robustness_score"] = score_pass
        if not score_pass:
            reasons.append(
                f"Robustness score below threshold: {report.robustness_score:.1f} < {min_robustness_score}"
            )

        all_approved = all(checks.values())
        status = "APPROVED" if all_approved else "REJECTED"

        if not all_approved:
            logger.warning(
                f"Strategy {report.strategy_slug} ({report.version}) PROMOTION REJECTED: {'; '.join(reasons)}"
            )
        else:
            logger.info(
                f"Strategy {report.strategy_slug} ({report.version}) PROMOTION APPROVED with Robustness {report.robustness_score:.1f}"
            )

        return PromotionEvaluationResult(
            approved=all_approved,
            status=status,
            strategy_slug=report.strategy_slug,
            version=report.version,
            reasons=reasons,
            criteria_checks=checks,
        )
