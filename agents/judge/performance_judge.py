"""
LLM as Judge & Performance Optimization Agent.
Inspired by Aslan11/crypto-trading-agents.

Evaluates system trading performance (Sharpe, Drawdown, Win Rate, Daily PnL)
and dynamically recommends risk profile adjustments (DEFENSIVE, BALANCED, AGGRESSIVE)
to preserve capital and avoid catastrophic drawdowns.
"""

import json
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from core.llm.llm_provider import BaseLLMProvider, get_llm_provider
from shared.logging import get_logger

logger = get_logger("performance-judge", service="agent")


class JudgeEvaluation(BaseModel):
    regime: str = Field(description="DEFENSIVE, BALANCED, or AGGRESSIVE")
    risk_multiplier: float = Field(ge=0.2, le=1.5, default=1.0)
    score: float = Field(ge=0.0, le=100.0, default=75.0)
    evaluation_summary: str
    actionable_directives: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    evaluator_engine: str = Field(
        default="QUANTITATIVE_HEURISTIC_RULE_ENGINE",
        description="Explicit attribution: QUANTITATIVE_HEURISTIC_RULE_ENGINE or EXTERNAL_LLM",
    )


class PerformanceJudgeAgent:
    """
    Supervisory Agent evaluating live/paper performance metrics.
    """

    def __init__(self, llm_provider: Optional[BaseLLMProvider] = None):
        self.llm = llm_provider or get_llm_provider()

    async def evaluate_performance(
        self,
        daily_pnl: float,
        daily_max_loss: float,
        total_trades: int,
        win_rate: float,
        max_drawdown_pct: float,
        current_streak: int = 0,
    ) -> JudgeEvaluation:
        """
        Evaluates current portfolio health and dictates risk multiplier.
        """
        # Formulate prompt for judge
        system_prompt = (
            "You are the Head Risk Judge & Quantitative Portfolio Supervisor. "
            "Your role is to protect capital during drawdowns and allow controlled expansion during favorable conditions. "
            "Output JSON with keys: 'regime' (DEFENSIVE, BALANCED, or AGGRESSIVE), "
            "'risk_multiplier' (0.3 to 1.3), 'score' (0-100), 'evaluation_summary', 'actionable_directives'."
        )

        user_prompt = (
            f"Current Portfolio Health:\n"
            f"- Daily Realized PnL: ${daily_pnl:.2f} (Daily Max Loss: ${daily_max_loss:.2f})\n"
            f"- Total Completed Trades: {total_trades}\n"
            f"- Win Rate: {win_rate:.1%}\n"
            f"- Max Drawdown: {max_drawdown_pct:.2%}\n"
            f"- Current Trade Win/Loss Streak: {current_streak}\n"
            "Assess current operational risk and output regime adjustment."
        )

        try:
            raw_res = await self.llm.generate(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.1,
                json_mode=True,
            )
            data = json.loads(raw_res)

            regime = str(data.get("regime", "BALANCED")).upper()
            if regime not in ("DEFENSIVE", "BALANCED", "AGGRESSIVE"):
                regime = "BALANCED"

            risk_mult = float(data.get("risk_multiplier", 1.0))
            risk_mult = max(0.2, min(1.3, risk_mult))

            score = float(data.get("score", 75.0))
            score = max(0.0, min(100.0, score))

            directives = data.get("actionable_directives", [])
            if isinstance(directives, str):
                directives = [directives]
            engine_label = getattr(self.llm, "engine_name", "EXTERNAL_LLM")

            return JudgeEvaluation(
                regime=regime,
                risk_multiplier=round(risk_mult, 2),
                score=round(score, 1),
                evaluation_summary=data.get("evaluation_summary", "Evaluation complete."),
                actionable_directives=directives,
                metadata={"daily_pnl": daily_pnl, "win_rate": win_rate, "drawdown": max_drawdown_pct},
                evaluator_engine=engine_label,
            )

        except Exception as e:
            logger.debug(f"Judge LLM fallback used: {e}")
            # Fallback deterministic evaluation
            if daily_pnl <= -0.6 * daily_max_loss or max_drawdown_pct > 0.05 or current_streak <= -2:
                regime = "DEFENSIVE"
                risk_mult = 0.50
                score = 45.0
                summary = "Drawdown or consecutive losses detected. Sizing down by 50% for capital preservation."
                directives = ["Halve base trade size", "Increase signal score threshold to 80%"]
            elif win_rate >= 0.65 and max_drawdown_pct < 0.02 and current_streak >= 3:
                regime = "AGGRESSIVE"
                risk_mult = 1.20
                score = 90.0
                summary = "Strong win streak and negligible drawdown. Allowing controlled size boost."
                directives = ["Scale standard position size up to 1.20x", "Tighten trailing stop to lock gains"]
            else:
                regime = "BALANCED"
                risk_mult = 1.0
                score = 75.0
                summary = "Portfolio operating normally within standard risk limits."
                directives = ["Maintain standard risk per trade (0.5%)"]

            return JudgeEvaluation(
                regime=regime,
                risk_multiplier=risk_mult,
                score=score,
                evaluation_summary=summary,
                actionable_directives=directives,
                metadata={"rule_fallback": True, "daily_pnl": daily_pnl},
                evaluator_engine="QUANTITATIVE_HEURISTIC_RULE_ENGINE",
            )


# Global Singleton
performance_judge = PerformanceJudgeAgent()
