import pytest
from core.llm.llm_provider import HeuristicFallbackProvider
from agents.judge.performance_judge import PerformanceJudgeAgent, JudgeEvaluation


@pytest.mark.asyncio
async def test_performance_judge_defensive():
    judge = PerformanceJudgeAgent(llm_provider=HeuristicFallbackProvider())

    # High drawdown, close to daily max loss ($50)
    eval_res: JudgeEvaluation = await judge.evaluate_performance(
        daily_pnl=-35.0,
        daily_max_loss=50.0,
        total_trades=8,
        win_rate=0.25,
        max_drawdown_pct=0.06,
        current_streak=-3,
    )

    assert isinstance(eval_res, JudgeEvaluation)
    assert eval_res.regime == "DEFENSIVE"
    assert eval_res.risk_multiplier <= 0.60
    assert len(eval_res.actionable_directives) > 0


@pytest.mark.asyncio
async def test_performance_judge_balanced():
    judge = PerformanceJudgeAgent(llm_provider=HeuristicFallbackProvider())

    eval_res: JudgeEvaluation = await judge.evaluate_performance(
        daily_pnl=10.0,
        daily_max_loss=50.0,
        total_trades=5,
        win_rate=0.50,
        max_drawdown_pct=0.015,
        current_streak=1,
    )

    assert eval_res.regime == "BALANCED"
    assert eval_res.risk_multiplier == 1.0


@pytest.mark.asyncio
async def test_performance_judge_aggressive():
    judge = PerformanceJudgeAgent(llm_provider=HeuristicFallbackProvider())

    eval_res: JudgeEvaluation = await judge.evaluate_performance(
        daily_pnl=85.0,
        daily_max_loss=50.0,
        total_trades=12,
        win_rate=0.75,
        max_drawdown_pct=0.008,
        current_streak=4,
    )

    assert eval_res.regime == "AGGRESSIVE"
    assert eval_res.risk_multiplier >= 1.10
