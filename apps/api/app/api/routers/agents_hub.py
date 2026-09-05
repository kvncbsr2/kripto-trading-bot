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
from apps.api.app.api.state import RUNTIME_STATE, market_data_service, paper_broker, risk_engine
from services.analytics.trade_reflection import trade_reflection_engine
from services.feature_engine.features import FeatureEngine
from services.market_data.binance_derivatives import derivatives_service
from services.market_data.crypto_sentiment import sentiment_service
from shared.enums import Timeframe
from shared.logging import get_logger

router = APIRouter(prefix="", tags=["Agents & Intelligence"])
logger = get_logger("agents-hub", service="agents_hub")

debate_engine = BullBearDebateEngine()


@router.get("/api/v1/agents/debate")
async def get_bull_bear_debate(symbol: str = Query("BTC/USDT", description="Trading pair symbol")) -> Dict[str, Any]:
    """
    Runs a structured Bull vs Bear dialectic debate for the specified symbol.

    FIX (2026-09): This endpoint previously fed the debate engine hardcoded
    context (current_price=0.0, rsi=50.0, ema_20/50=0.0) regardless of the
    requested symbol — a direct violation of the project's own "Zero Fake Data"
    policy. It now pulls a real live ticker + real 15m candles from Binance via
    MarketDataService and computes real RSI/EMA20/EMA50/realized_vol via
    FeatureEngine, exactly like the live trading path does. If real data cannot
    be fetched, the endpoint is transparent about it (data_quality=UNAVAILABLE)
    instead of fabricating numbers.
    """
    symbol = symbol.replace("-", "/").upper()
    deriv_data = await derivatives_service.get_derivatives_metrics(symbol)
    fng_data = await sentiment_service.get_fear_and_greed_index()

    data_quality = "REAL_BINANCE"
    current_price = 0.0
    features: Dict[str, Any] = {"rsi": None, "ema_20": None, "ema_50": None, "realized_vol": None}
    orderbook_ctx: Dict[str, Any] = {"spread_bps": None}

    try:
        ticker = await market_data_service.get_live_ticker(symbol)
        candles = await market_data_service.get_historical_klines(symbol, timeframe="15m", limit=100)
        orderbook = await market_data_service.get_orderbook(symbol, limit=10)

        if ticker and ticker.get("price"):
            current_price = float(ticker["price"])
        else:
            data_quality = "UNAVAILABLE"

        if candles and len(candles) >= 5:
            fv = FeatureEngine.get_latest_feature_vector(candles, symbol=symbol, timeframe=Timeframe.M15)
            if fv is not None:
                features = {
                    "rsi": fv.indicators.get("rsi"),
                    "ema_20": fv.indicators.get("ema_20"),
                    "ema_50": fv.indicators.get("ema_50"),
                    "realized_vol": fv.indicators.get("realized_vol"),
                }
            else:
                data_quality = "UNAVAILABLE"
        else:
            data_quality = "UNAVAILABLE"

        if orderbook and orderbook.get("spread_bps") is not None:
            orderbook_ctx = {"spread_bps": orderbook["spread_bps"]}
        else:
            data_quality = "UNAVAILABLE"
    except Exception as e:
        logger.error(f"Failed to build real debate context for {symbol}: {e}")
        data_quality = "UNAVAILABLE"

    # BullBearDebateEngine's rule-based fallback does numeric formatting (f"{rsi:.1f}")
    # on these fields with no None-check, so on UNAVAILABLE we still pass its own
    # neutral defaults through — but data_quality below tells the caller these are
    # NOT real numbers, unlike before where this was silently indistinguishable.
    safe_features = {
        "rsi": features["rsi"] if features["rsi"] is not None else 50.0,
        "ema_20": features["ema_20"] if features["ema_20"] is not None else 0.0,
        "ema_50": features["ema_50"] if features["ema_50"] is not None else 0.0,
        "realized_vol": features["realized_vol"] if features["realized_vol"] is not None else 0.35,
    }
    safe_orderbook = {"spread_bps": orderbook_ctx["spread_bps"] if orderbook_ctx["spread_bps"] is not None else 2.0}

    context = {
        "symbol": symbol,
        "current_price": current_price,
        "features": safe_features,
        "sentiment": fng_data,
        "derivatives": deriv_data,
        "orderbook": safe_orderbook,
        "data_quality": data_quality,
    }

    verdict = await debate_engine.run_debate(context)
    return {
        "status": "success",
        "symbol": symbol,
        "data_quality": data_quality,
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
