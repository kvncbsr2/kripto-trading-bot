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
    risk_per_trade: Optional[float] = Field(None, ge=0.001, le=0.05)
    daily_max_loss_usd: Optional[float] = Field(None, ge=10.0, le=500.0)
    max_open_positions: Optional[int] = Field(None, ge=1, le=25)


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
        risk_engine.max_open_positions = payload.max_open_positions

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
