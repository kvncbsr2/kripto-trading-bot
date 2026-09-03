from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models.tables import FillModel, OrderModel
from shared.schemas import Fill, Order


class OrderRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def save_order(self, order: Order) -> OrderModel:
        query = select(OrderModel).where(OrderModel.order_id == order.order_id)
        res = await self.session.execute(query)
        existing = res.scalar_one_or_none()

        if existing:
            existing.status = (
                order.status.value if hasattr(order.status, "value") else str(order.status)
            )
            existing.filled_quantity = order.filled_quantity
            existing.average_fill_price = order.average_fill_price
            existing.fee_paid = order.fee_paid
            existing.updated_at = datetime.now(timezone.utc)
            return existing

        db_order = OrderModel(
            order_id=order.order_id,
            symbol=order.symbol,
            order_type=order.order_type.value
            if hasattr(order.order_type, "value")
            else str(order.order_type),
            side=order.side.value if hasattr(order.side, "value") else str(order.side),
            quantity=order.quantity,
            price=order.price,
            stop_price=order.stop_price,
            status=order.status.value if hasattr(order.status, "value") else str(order.status),
            filled_quantity=order.filled_quantity,
            average_fill_price=order.average_fill_price,
            fee_paid=order.fee_paid,
        )
        self.session.add(db_order)
        return db_order

    async def save_fill(self, fill: Fill) -> FillModel:
        db_fill = FillModel(
            fill_id=fill.fill_id,
            order_id=fill.order_id,
            symbol=fill.symbol,
            side=fill.side.value if hasattr(fill.side, "value") else str(fill.side),
            price=fill.price,
            quantity=fill.quantity,
            fee=fill.fee,
            slippage=fill.slippage,
            timestamp=fill.timestamp,
        )
        self.session.add(db_fill)
        return db_fill

    async def get_orders(self, symbol: Optional[str] = None, limit: int = 100) -> List[OrderModel]:
        stmt = select(OrderModel)
        if symbol:
            stmt = stmt.where(OrderModel.symbol == symbol)
        stmt = stmt.order_by(OrderModel.created_at.desc()).limit(limit)
        res = await self.session.execute(stmt)
        return list(res.scalars().all())
