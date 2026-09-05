"""
Multi-Agent Hub Router for KRIPTO AGENT.
Exposes endpoints for:
- Bull vs Bear Debate Engine
- Episodic Trade Reflection Memory
- LLM Performance Judge Agent
- Crypto Sentiment & Futures Derivatives
"""

from typing import Any, Dict

from fastapi import APIRouter, Query

from agents.debate.bull_bear_engine import BullBearDebateEngine
from agents.judge.performance_judge import performance_judge
from apps.api.app.api.state import RUNTIME_STATE, paper_broker, risk_engine
from services.analytics.trade_reflection import trade_reflection_engine
from services.market_data.binance_derivatives import derivatives_service
from services.market_data.crypto_sentiment import sentiment_service

router = APIRouter(prefix="", tags=["Agents & Intelligence"])

debate_engine = BullBearDebateEngine()


@router.get("/api/v1/agents/debate")
async def get_bull_bear_debate(symbol: str = Query("BTC/USDT", description="Trading pair symbol")) -> Dict[str, Any]:
    """
    Runs a structured Bull vs Bear dialectic debate for the specified symbol.
    """
    deriv_data = await derivatives_service.get_derivatives_metrics(symbol)
    fng_data = await sentiment_service.get_fear_and_greed_index()

    context = {
        "symbol": symbol,
        "current_price": 0.0,
        "features": {"rsi": 50.0, "ema_20": 0.0, "ema_50": 0.0, "realized_vol": 0.35},
        "sentiment": fng_data,
        "derivatives": deriv_data,
        "orderbook": {"spread_bps": 2.0},
    }

    verdict = await debate_engine.run_debate(context)
    return {
        "status": "success",
        "symbol": symbol,
        "verdict": verdict.model_dump(),
    }


@router.get("/api/v1/analytics/reflections")
async def get_trade_reflections(limit: int = Query(10, ge=1, le=50)) -> Dict[str, Any]:
    """
    Returns the recent episodic trade reflection records and lessons learned.
    """
    reflections = trade_reflection_engine.get_recent_reflections(limit=limit)
    return {
        "status": "success",
        "total_records": len(reflections),
        "reflections": reflections,
    }


@router.get("/api/v1/agents/judge")
async def get_judge_evaluation() -> Dict[str, Any]:
    """
    Runs or retrieves the LLM as Judge performance assessment.
    """
    daily_pnl = float(RUNTIME_STATE.get("daily_pnl", 0.0))
    daily_max_loss = getattr(risk_engine, "daily_max_loss_usd", 50.0)

    # Compute win rate and stats from paper broker
    closed = getattr(paper_broker, "closed_positions_history", [])
    total_trades = len(closed)
    wins = sum(1 for p in closed if getattr(p, "realized_pnl", 0.0) > 0)
    win_rate = (wins / total_trades) if total_trades > 0 else 0.50

    eval_res = await performance_judge.evaluate_performance(
        daily_pnl=daily_pnl,
        daily_max_loss=daily_max_loss,
        total_trades=total_trades,
        win_rate=win_rate,
        max_drawdown_pct=0.01,
        current_streak=1 if daily_pnl >= 0 else -1,
    )

    return {
        "status": "success",
        "evaluation": eval_res.model_dump(),
    }


@router.get("/api/v1/market/sentiment-metrics")
async def get_sentiment_and_derivatives(symbol: str = Query("BTC/USDT")) -> Dict[str, Any]:
    """
    Consolidated real crypto sentiment & institutional derivatives indicators.
    """
    fng = await sentiment_service.get_fear_and_greed_index()
    deriv = await derivatives_service.get_derivatives_metrics(symbol)

    return {
        "status": "success",
        "symbol": symbol,
        "fear_and_greed": fng,
        "derivatives": deriv,
    }
