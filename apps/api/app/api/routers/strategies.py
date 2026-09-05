from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from apps.api.app.api.state import command_bus
from apps.api.app.middleware.auth import Role, verify_api_key_or_token

router = APIRouter(tags=["strategies"])


class StrategyToggleRequest(BaseModel):
    enabled: bool


KNOWN_STRATEGIES = {
    "r10_rsi_divergence": {
        "name": "R10 RSI Divergence Swing",
        "family": "R10_DIVERGENCE",
        "description": "Strictly causal RSI divergence with T+5 bar confirmation window and ATR-based stops.",
        "enabled": True,
        "timeframe": "15m",
    },
    "trend_following": {
        "name": "Trend Following EMA Breakout",
        "family": "TREND",
        "description": "EMA ribbon trend alignment with dynamic trailing lock.",
        "enabled": False,
        "timeframe": "1h",
    },
    "mean_reversion": {
        "name": "Bollinger Bands Mean Reversion",
        "family": "MEAN_REVERSION",
        "description": "Mean reversion from extreme volatility bands.",
        "enabled": False,
        "timeframe": "15m",
    },
}


@router.get("/strategies")
@router.get("/api/v1/strategies")
@router.get("/api/strategies")
async def get_strategies():
    strats = []
    for key, val in KNOWN_STRATEGIES.items():
        item = dict(val)
        item["id"] = key
        strats.append(item)
    return strats


@router.get("/strategies/{name}")
@router.get("/api/v1/strategies/{name}")
@router.get("/api/strategies/{name}")
async def get_strategy(name: str):
    slug = name.lower()
    if slug not in KNOWN_STRATEGIES:
        raise HTTPException(status_code=404, detail=f"Strategy {name} not found.")
    res = dict(KNOWN_STRATEGIES[slug])
    res["id"] = slug
    return res


@router.post("/strategies/{name}/toggle")
@router.post("/api/strategies/{name}/toggle")
async def post_toggle_strategy(
    name: str,
    payload: StrategyToggleRequest,
    _role: Role = Depends(verify_api_key_or_token),
):
    slug = name.lower()
    if slug not in KNOWN_STRATEGIES:
        raise HTTPException(status_code=404, detail=f"Strategy {name} not found.")
    KNOWN_STRATEGIES[slug]["enabled"] = payload.enabled
    return {"strategy": slug, "enabled": payload.enabled}


@router.post("/api/v1/strategies/{name}/promote")
async def post_promote_strategy(
    name: str,
    _role: Role = Depends(verify_api_key_or_token),
):
    slug = name.lower()
    if slug in KNOWN_STRATEGIES:
        KNOWN_STRATEGIES[slug]["enabled"] = True
    return {
        "success": True,
        "strategy_id": slug,
        "status": "PAPER_TRADING",
        "message": f"Strategy {name} promoted to paper trading.",
    }


@router.get("/strategies/performance")
async def get_strategies_performance():
    """
    Computes REAL strategy performance from authoritative executed trade history.
    Zero fake/hardcoded win rates or trade counts.
    """
    broker = command_bus.broker
    closed = broker.closed_positions_history if broker else []

    performance: Dict[str, Any] = {}
    for key in KNOWN_STRATEGIES:
        strat_trades = [p for p in closed if p.strategy and key in p.strategy.lower()]
        if not strat_trades:
            performance[key] = {
                "trades": 0,
                "win_rate": 0.0,
                "net_pnl": 0.0,
                "status": "NO_DATA",
            }
        else:
            wins = [p for p in strat_trades if p.realized_pnl > 0]
            net_pnl = sum(p.realized_pnl for p in strat_trades)
            win_rate = (len(wins) / len(strat_trades)) * 100.0
            performance[key] = {
                "trades": len(strat_trades),
                "win_rate": round(win_rate, 1),
                "net_pnl": round(net_pnl, 2),
                "status": "ACTIVE",
            }

    return performance
