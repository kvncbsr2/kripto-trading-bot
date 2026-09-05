from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.api.state import command_bus, market_data_service
from apps.api.app.middleware.auth import Role, verify_api_key_or_token
from database.models.tables import PositionModel
from database.session import get_async_db

router = APIRouter(tags=["positions"])


class ClosePositionRequest(BaseModel):
    reason: str = "USER_MANUAL_CLOSE"


@router.get("/positions")
@router.get("/api/v1/positions")
@router.get("/api/v1/positions/active")
async def get_positions(status: str = "OPEN", db: AsyncSession = Depends(get_async_db)):
    """
    Returns active or historical positions from DB and authoritative PaperBroker.
    Strictly Zero Fake Data: Returns empty list if no positions exist.
    """
    positions = []
    try:
        stmt = select(PositionModel)
        if status.upper() != "ALL":
            stmt = stmt.where(PositionModel.status == status.upper())
        stmt = stmt.order_by(PositionModel.created_at.desc())
        res = await db.execute(stmt)
        positions = list(res.scalars().all())
    except Exception:
        pass

    pos_list = [
        {
            "position_id": p.position_id,
            "symbol": p.symbol,
            "side": p.side,
            "entry_price": p.entry_price,
            "current_price": p.current_price,
            "quantity": p.quantity,
            "stop_loss": p.stop_loss,
            "take_profit": p.take_profit,
            "unrealized_pnl": p.unrealized_pnl,
            "realized_pnl": p.realized_pnl,
            "status": p.status,
            "strategy": p.strategy,
            "opened_at": p.created_at.isoformat() if p.created_at else None,
            "closed_at": p.closed_at.isoformat() if p.closed_at else None,
        }
        for p in positions
    ]

    # Include in-memory paper broker open positions
    if command_bus.broker:
        for symbol, p in command_bus.broker.open_positions.items():
            if not any(x["position_id"] == p.position_id for x in pos_list):
                pos_list.append({
                    "position_id": p.position_id,
                    "symbol": p.symbol,
                    "side": p.side.value if hasattr(p.side, "value") else str(p.side),
                    "entry_price": p.entry_price,
                    "current_price": p.current_price,
                    "quantity": p.quantity,
                    "stop_loss": p.stop_loss,
                    "take_profit": p.take_profit,
                    "unrealized_pnl": p.unrealized_pnl,
                    "realized_pnl": p.realized_pnl,
                    "status": p.status.value if hasattr(p.status, "value") else str(p.status),
                    "strategy": p.strategy,
                    "opened_at": p.opened_at.isoformat() if hasattr(p.opened_at, "isoformat") else str(p.opened_at),
                    "closed_at": None,
                })

    return pos_list


@router.get("/positions/{symbol:path}")
async def get_position_by_symbol(symbol: str):
    norm_symbol = symbol.replace("-", "/").upper()
    if command_bus.broker and norm_symbol in command_bus.broker.open_positions:
        p = command_bus.broker.open_positions[norm_symbol]
        return {
            "position_id": p.position_id,
            "symbol": p.symbol,
            "side": p.side.value,
            "entry_price": p.entry_price,
            "current_price": p.current_price,
            "quantity": p.quantity,
            "stop_loss": p.stop_loss,
            "take_profit": p.take_profit,
            "unrealized_pnl": p.unrealized_pnl,
            "status": p.status.value,
        }
    raise HTTPException(status_code=404, detail=f"No active position for {norm_symbol}")


@router.post("/positions/{identifier:path}/close")
@router.post("/api/v1/positions/{identifier:path}/close")
async def post_close_position(
    identifier: str,
    payload: Optional[ClosePositionRequest] = None,
    _role: Role = Depends(verify_api_key_or_token),
):
    """
    Closes an open position at the REAL current market price.
    """
    if not command_bus.broker:
        raise HTTPException(status_code=500, detail="Broker not initialized.")

    norm_symbol = identifier.replace("-", "/").upper()
    broker = command_bus.broker

    # Find position by symbol or position_id
    target_pos = None
    target_symbol = None
    for sym, pos in broker.open_positions.items():
        if sym == norm_symbol or pos.position_id == identifier:
            target_pos = pos
            target_symbol = sym
            break

    if not target_pos or not target_symbol:
        raise HTTPException(status_code=404, detail=f"No active position found for {identifier}")

    # Fetch real live price from MarketDataService
    ticker = await market_data_service.get_live_ticker(target_symbol)
    exit_price = float(ticker["price"]) if ticker and "price" in ticker else target_pos.current_price

    reason = payload.reason if payload else "USER_MANUAL_CLOSE"
    closed = broker.close_position(symbol=target_symbol, exit_price=exit_price, reason=reason)

    if not closed:
        raise HTTPException(status_code=500, detail="Failed to close position.")

    return {
        "success": True,
        "action": "POSITION_CLOSED",
        "symbol": closed.symbol,
        "exit_price": closed.current_price,
        "realized_pnl": closed.realized_pnl,
        "reason": reason,
        "message": f"{closed.symbol} pozisyonu ${closed.current_price:.2f} fiyattan kapatıldı. Kâr/Zarar: ${closed.realized_pnl:+.2f}",
    }


@router.get("/api/v1/positions/history")
@router.get("/position-history")
async def get_position_history():
    """
    Returns closed position history from authoritative PaperBroker.
    Zero Fake Data Policy: If no trades exist, returns empty history.
    """
    history_records = []
    if command_bus.broker and command_bus.broker.closed_positions_history:
        for pos in reversed(command_bus.broker.closed_positions_history):
            exit_price = pos.current_price or pos.entry_price
            cost_basis = pos.entry_price * pos.quantity
            roi_pct = (pos.realized_pnl / cost_basis * 100.0) if cost_basis > 0 else 0.0
            opened_str = (
                pos.opened_at.strftime("%d/%m/%Y %H:%M:%S")
                if hasattr(pos.opened_at, "strftime")
                else str(pos.opened_at)
            )
            closed_str = (
                pos.closed_at.strftime("%d/%m/%Y %H:%M:%S")
                if pos.closed_at and hasattr(pos.closed_at, "strftime")
                else datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M:%S")
            )

            history_records.append({
                "position_id": pos.position_id,
                "symbol": pos.symbol,
                "clean_symbol": pos.symbol.replace("/", ""),
                "side": pos.side.value if hasattr(pos.side, "value") else str(pos.side),
                "leverage": "1x (Spot)",
                "margin_mode": "Spot Long",
                "status": "Closed",
                "realized_pnl": round(pos.realized_pnl, 2),
                "roi_pct": round(roi_pct, 2),
                "closed_volume_usd": round(exit_price * pos.quantity, 2),
                "entry_price": pos.entry_price,
                "exit_price": exit_price,
                "quantity": pos.quantity,
                "opened_at": opened_str,
                "closed_at": closed_str,
                "is_win": pos.realized_pnl > 0,
            })

    total_pnl = sum(r["realized_pnl"] for r in history_records)
    total_wins = sum(1 for r in history_records if r["is_win"])
    total_losses = sum(1 for r in history_records if not r["is_win"])
    win_rate = (total_wins / len(history_records) * 100.0) if history_records else 0.0

    return {
        "summary": {
            "total_realized_pnl": round(total_pnl, 2),
            "total_trades": len(history_records),
            "wins": total_wins,
            "losses": total_losses,
            "win_rate_pct": round(win_rate, 1),
        },
        "history": history_records,
    }
