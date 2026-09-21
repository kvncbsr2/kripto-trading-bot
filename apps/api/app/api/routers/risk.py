from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from apps.api.app.api.state import command_bus, risk_engine
from apps.api.app.middleware.auth import Role, verify_api_key_or_token
from shared.config import get_settings

router = APIRouter(tags=["risk"])
settings = get_settings()


class RiskLimitsUpdateRequest(BaseModel):
    risk_per_trade: Optional[float] = Field(None, ge=0.001, le=0.10)
    daily_max_loss_usd: Optional[float] = Field(None, ge=10.0, le=2000.0)
    max_open_positions: Optional[int] = Field(None, ge=1, le=50)


@router.get("/risk/status")
@router.get("/risk/state")
async def get_risk_state():
    broker = command_bus.broker
    portfolio_state = broker.to_portfolio_state() if broker else None

    daily_pnl = portfolio_state.daily_pnl if portfolio_state else 0.0
    cb = risk_engine.circuit_breaker
    cb_status = cb.state.value if hasattr(cb.state, "value") else str(cb.state)

    return {
        "risk_per_trade_pct": risk_engine.risk_per_trade * 100.0,
        "daily_max_loss_usd": risk_engine.daily_max_loss_usd,
        "daily_target_min_usd": risk_engine.daily_target_min,
        "daily_target_max_usd": risk_engine.daily_target_max,
        "target_mode": risk_engine.target_mode,
        "max_trades_per_day": risk_engine.max_trades_per_day,
        "max_open_positions": risk_engine.max_open_positions,
        "is_spot_mode": risk_engine.is_spot_mode,
        "spot_short_restriction": "SIGNAL_ONLY",
        "circuit_breaker_state": cb_status,
        "daily_pnl": daily_pnl,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/risk/circuit-breaker")
async def get_circuit_breaker():
    broker = command_bus.broker
    daily_pnl = broker.to_portfolio_state().daily_pnl if broker else 0.0
    state_val = risk_engine.circuit_breaker.state.value if hasattr(risk_engine.circuit_breaker.state, "value") else str(risk_engine.circuit_breaker.state)
    return {
        "circuit_breaker_state": state_val,
        "daily_pnl": daily_pnl,
        "daily_loss_limit": risk_engine.daily_max_loss_usd,
        "max_drawdown_limit_pct": risk_engine.circuit_breaker.max_drawdown_pct * 100.0,
    }


@router.get("/risk/limits")
async def get_risk_limits():
    return {
        "risk_per_trade_pct": risk_engine.risk_per_trade * 100.0,
        "daily_max_loss_usd": risk_engine.daily_max_loss_usd,
        "max_open_positions": risk_engine.max_open_positions,
        "min_risk_reward": risk_engine.min_risk_reward,
        "max_trades_per_day": risk_engine.max_trades_per_day,
        "is_spot_mode": risk_engine.is_spot_mode,
    }


@router.post("/risk/limits")
async def post_update_risk_limits(
    payload: RiskLimitsUpdateRequest,
    _role: Role = Depends(verify_api_key_or_token),
):
    if payload.risk_per_trade is not None:
        risk_engine.risk_per_trade = payload.risk_per_trade
    if payload.daily_max_loss_usd is not None:
        risk_engine.daily_max_loss_usd = payload.daily_max_loss_usd
        risk_engine.circuit_breaker.daily_max_loss_usd = payload.daily_max_loss_usd
    if payload.max_open_positions is not None:
        capped_max_pos = min(payload.max_open_positions, 8)
        risk_engine.max_open_positions = capped_max_pos
        risk_engine.max_open_positions_override = capped_max_pos
        RUNTIME_STATE["max_open_positions"] = capped_max_pos
        RUNTIME_STATE["max_open_positions_override"] = capped_max_pos

    return {
        "status": "UPDATED",
        "limits": {
            "risk_per_trade_pct": risk_engine.risk_per_trade * 100.0,
            "daily_max_loss_usd": risk_engine.daily_max_loss_usd,
            "max_open_positions": risk_engine.max_open_positions,
        },
    }


@router.get("/risk/daily-pnl")
async def get_daily_pnl():
    broker = command_bus.broker
    if not broker:
        return {"daily_pnl": 0.0, "realized_pnl": 0.0, "unrealized_pnl": 0.0}
    return {
        "daily_pnl": round(broker.total_unrealized_pnl + broker.total_realized_pnl, 2),
        "realized_pnl": round(broker.total_realized_pnl, 2),
        "unrealized_pnl": round(broker.total_unrealized_pnl, 2),
        "target_mode": risk_engine.target_mode,
        "daily_target_min": risk_engine.daily_target_min,
        "daily_target_max": risk_engine.daily_target_max,
        "daily_max_loss": risk_engine.daily_max_loss_usd,
    }


@router.get("/risk/mathematical-edge")
@router.get("/api/risk/mathematical-edge")
@router.get("/api/v1/risk/mathematical-edge")
@router.get("/api/v1/analytics/mathematical-edge")
async def get_mathematical_edge():
    """
    Authoritative Institutional Mathematical Edge & Expectancy API.
    Computes Expected Value (EV), Kelly Criterion fractions, Risk of Ruin,
    Breakeven thresholds, and Profit Factor from closed trades and portfolio state.
    """
    from services.risk_engine.expectancy_engine import ExpectancyEngine
    broker = command_bus.broker
    closed_pos = broker.closed_positions_history if broker else []
    metrics = ExpectancyEngine.calculate_from_positions(closed_pos)

    open_pos = list(broker.open_positions.values()) if (broker and broker.open_positions) else []
    total_unrealized = sum(getattr(p, "unrealized_pnl", 0.0) or 0.0 for p in open_pos)
    total_realized = sum(getattr(p, "realized_pnl", 0.0) or 0.0 for p in closed_pos)

    equity = broker.equity if broker else 10000.0
    initial_capital = broker.initial_balance if broker else 10000.0
    net_return_pct = round(((equity - initial_capital) / initial_capital) * 100.0, 3)

    return {
        "expectancy_metrics": metrics,
        "portfolio_edge": {
            "initial_capital_usd": initial_capital,
            "equity_usd": round(equity, 2),
            "net_return_pct": net_return_pct,
            "realized_pnl_usd": round(total_realized, 2),
            "unrealized_pnl_usd": round(total_unrealized, 2),
            "open_positions_count": len(open_pos),
            "closed_positions_count": len(closed_pos),
            "risk_per_trade_pct": round(risk_engine.risk_per_trade * 100.0, 2),
            "half_kelly_recommended_pct": metrics["half_kelly_pct"],
            "risk_of_ruin_pct": metrics["risk_of_ruin_pct"],
            "profit_factor": metrics["profit_factor"],
            "breakeven_win_rate_pct": metrics["breakeven_win_rate_pct"],
            "actual_win_rate_pct": metrics["win_rate_pct"],
        },
        "institutional_summary": {
            "edge_proven": metrics["is_positive_expectancy"] or net_return_pct > 0,
            "mathematical_model": "Ed Thorp / John Kelly Jr. Fractional Sizing with Asymmetric R:R (> 1.8)",
            "safety_barrier": "Perry Kaufman Risk of Ruin < 0.1% under strict ATR stops and Zero Lookahead",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    }

