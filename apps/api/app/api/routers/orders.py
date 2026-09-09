from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.api.state import command_bus, market_data_service, risk_engine
from apps.api.app.middleware.auth import Role, verify_api_key_or_token
from database.models.tables import FillModel, OrderModel
from database.session import get_async_db
from services.execution.order_manager import order_manager
from shared.enums import SignalDirection
from shared.schemas import Signal

router = APIRouter(tags=["orders"])


class SimulateTradeRequest(BaseModel):
    symbol: str = "BTC/USDT"
    side: str = "BUY"  # BUY or SELL
    amount_usd: float = Field(250.0, ge=10.0, le=5000.0)
    quantity: Optional[float] = None


@router.get("/orders")
async def get_orders(limit: int = 50, db: AsyncSession = Depends(get_async_db)):
    stmt = select(OrderModel).order_by(OrderModel.created_at.desc()).limit(limit)
    res = await db.execute(stmt)
    orders = list(res.scalars().all())
    order_list = [
        {
            "order_id": o.order_id,
            "symbol": o.symbol,
            "order_type": o.order_type,
            "side": o.side,
            "quantity": o.quantity,
            "price": o.price,
            "status": o.status,
            "average_fill_price": o.average_fill_price,
            "fee_paid": o.fee_paid,
            "created_at": o.created_at.isoformat(),
        }
        for o in orders
    ]

    # Include in-memory broker orders
    if command_bus.broker:
        for oid, o in command_bus.broker.orders.items():
            if not any(x["order_id"] == oid for x in order_list):
                order_list.append({
                    "order_id": o.order_id,
                    "symbol": o.symbol,
                    "order_type": o.order_type.value,
                    "side": o.side.value,
                    "quantity": o.quantity,
                    "price": o.price,
                    "status": o.status.value,
                    "average_fill_price": o.average_fill_price,
                    "fee_paid": o.fee_paid,
                    "created_at": o.created_at.isoformat(),
                })

    order_list.sort(key=lambda x: str(x.get("created_at", "")), reverse=True)
    return order_list[:limit]


@router.get("/orders/{order_id}")
async def get_order_by_id(order_id: str):
    if command_bus.broker and order_id in command_bus.broker.orders:
        o = command_bus.broker.orders[order_id]
        return {
            "order_id": o.order_id,
            "symbol": o.symbol,
            "order_type": o.order_type.value,
            "side": o.side.value,
            "quantity": o.quantity,
            "price": o.price,
            "status": o.status.value,
            "average_fill_price": o.average_fill_price,
            "fee_paid": o.fee_paid,
        }
    raise HTTPException(status_code=404, detail=f"Order {order_id} not found.")


@router.delete("/orders/{order_id}")
@router.delete("/api/v1/orders/{order_id}")
@router.post("/orders/cancel/{order_id}")
@router.post("/api/v1/orders/cancel/{order_id}")
async def post_cancel_order(
    order_id: str,
    _role: Role = Depends(verify_api_key_or_token),
):
    if not command_bus.broker:
        raise HTTPException(status_code=500, detail="Broker not ready.")
    order_manager.bind_execution_engine(command_bus.broker)
    cancelled = await order_manager.cancel_order(order_id)
    if not cancelled:
        cancelled = await command_bus.broker.cancel_order(order_id)
    if not cancelled:
        raise HTTPException(status_code=404, detail="Order not found or already terminal.")
    return {"status": "CANCELLED", "order_id": order_id}


@router.get("/orders/stats")
async def get_order_stats():
    broker = command_bus.broker
    if not broker:
        return {"total_orders": 0, "total_fills": 0, "total_fees": 0.0, "total_slippage": 0.0}
    return {
        "total_orders": len(broker.orders),
        "total_fills": len(broker.fills),
        "total_fees": round(broker.total_fees_paid, 2),
        "total_slippage": round(broker.total_slippage_paid, 2),
    }


@router.get("/trades")
async def get_trades(limit: int = 50, db: AsyncSession = Depends(get_async_db)):
    """
    Returns real executed fills with ZERO fake data.
    If no trades exist yet, returns an empty list [].
    """
    stmt = select(FillModel).order_by(FillModel.timestamp.desc()).limit(limit)
    res = await db.execute(stmt)
    fills = list(res.scalars().all())
    trade_list = [
        {
            "fill_id": f.fill_id,
            "order_id": f.order_id,
            "symbol": f.symbol,
            "side": f.side,
            "price": f.price,
            "quantity": f.quantity,
            "fee": f.fee,
            "slippage": f.slippage,
            "total_usd": round(f.price * f.quantity, 2),
            "status": "EXECUTED",
            "execution_mode": "VIRTUAL_PAPER",
            "timestamp": f.timestamp.isoformat(),
        }
        for f in fills
    ]

    # Include in-memory Paper Broker fills from command bus
    if command_bus.broker and command_bus.broker.fills:
        for f in command_bus.broker.fills:
            ts_str = f.timestamp.isoformat() if hasattr(f.timestamp, "isoformat") else str(f.timestamp)
            if not any(x["fill_id"] == f.fill_id for x in trade_list):
                trade_list.append({
                    "fill_id": f.fill_id,
                    "order_id": f.order_id,
                    "symbol": f.symbol,
                    "side": f.side.value if hasattr(f.side, "value") else str(f.side),
                    "price": f.price,
                    "quantity": f.quantity,
                    "fee": f.fee,
                    "slippage": f.slippage,
                    "total_usd": round(f.price * f.quantity, 2),
                    "status": "EXECUTED",
                    "execution_mode": "VIRTUAL_PAPER",
                    "timestamp": ts_str,
                })

    trade_list.sort(key=lambda x: str(x.get("timestamp", "")), reverse=True)
    return trade_list[:limit]


@router.post("/orders")
@router.post("/api/v1/orders")
@router.post("/api/v1/trades/simulate")
@router.post("/api/v1/orders/paper/execute")
@router.post("/orders/paper/execute")
@router.post("/trades/simulate")
async def post_simulate_trade(
    payload: SimulateTradeRequest,
    _role: Role = Depends(verify_api_key_or_token),
):
    """
    Executes a real-market-priced simulated paper trade.
    Enforces RiskEngine validation and Spot mode constraints:
    - Real price from MarketDataService (Zero Fake Data)
    - SHORT is strictly rejected in Spot mode (SIGNAL_ONLY)
    - Mandatory RiskEngine gate
    - Executed strictly through centralized OrderManager (single authority)
    """
    if not command_bus.broker:
        raise HTTPException(status_code=500, detail="Broker not initialized.")

    symbol = payload.symbol.upper()
    side = payload.side.upper()
    is_buy = side in ["BUY", "LONG", "AL"]

    # 1. Fetch real market price
    ticker = await market_data_service.get_live_ticker(symbol)
    if not ticker or "price" not in ticker or ticker["price"] <= 0:
        raise HTTPException(
            status_code=400,
            detail=f"Real market price for {symbol} is currently unavailable from Binance",
        )
    live_price = float(ticker["price"])

    # 2. Check if user wants to close an existing position with a SELL
    broker = command_bus.broker
    if not is_buy and symbol in broker.open_positions:
        pos = broker.close_position(symbol, exit_price=live_price, reason="USER_MANUAL_SELL")
        pnl = pos.realized_pnl if pos else 0.0
        return {
            "success": True,
            "action": "POSITION_CLOSED",
            "symbol": symbol,
            "side": "SELL",
            "price": live_price,
            "realized_pnl": pnl,
            "equity": broker.equity,
            "message": f"{symbol} pozisyonu canlı ${live_price:.2f} fiyattan kapatıldı. Kâr/Zarar: ${pnl:+.2f}",
        }

    # 3. If Spot Mode and user attempts to open a new SHORT, reject!
    if not is_buy and risk_engine.is_spot_mode:
        raise HTTPException(
            status_code=400,
            detail="Spot Mode: SHORT is NOT_SUPPORTED (SIGNAL_ONLY). Live/paper shorting is disabled on Spot.",
        )

    # 4. Calculate quantity and risk levels
    qty = payload.quantity if (payload.quantity and payload.quantity > 0) else round(payload.amount_usd / live_price, 5)
    if qty <= 0:
        qty = 0.001

    signal = Signal(
        symbol=symbol,
        strategy="Manual_Panel_Trade",
        direction=SignalDirection.LONG if is_buy else SignalDirection.SHORT,
        entry_price=live_price,
        stop_price=round(live_price * 0.98, 4 if live_price < 10 else 2),
        take_profit=round(live_price + (abs(live_price - round(live_price * 0.98, 4 if live_price < 10 else 2)) * 2.1), 4 if live_price < 10 else 2),
        confidence=0.9,
    )

    # 5. Route through RiskEngine
    decision = risk_engine.evaluate_signal(signal=signal, portfolio=broker.to_portfolio_state())
    if not decision.approved:
        raise HTTPException(
            status_code=400,
            detail=f"Risk Engine Rejection: {decision.reason}",
        )

    # Overwrite size from user request if within allowed limits
    decision.calculated_size = min(qty, decision.calculated_size or qty)

    # 6. Execute Order via Single Authority OrderManager
    order_manager.bind_execution_engine(broker)
    order_manager.bind_risk_engine(risk_engine)
    order, fill, pos = await order_manager.execute_risk_decision(
        decision=decision,
        strategy_name="Manual_Paper_Execution",
        signal_id=f"manual_{symbol}_{int(datetime.now(timezone.utc).timestamp())}",
    )

    return {
        "success": True,
        "action": "ORDER_FILLED",
        "fill_id": fill.fill_id,
        "order_id": order.order_id,
        "symbol": fill.symbol,
        "side": fill.side.value,
        "price": fill.price,
        "quantity": fill.quantity,
        "fee": fill.fee,
        "slippage": fill.slippage,
        "total_usd": round(fill.price * fill.quantity, 2),
        "equity": broker.equity,
        "message": f"Sanal emir gerçekleşti: {side} {fill.quantity} {symbol} @ ${fill.price:.2f} (Komisyon: ${fill.fee:.2f})",
    }
