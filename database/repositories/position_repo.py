from datetime import datetime, timezone
from typing import List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models.tables import PositionModel
from shared.enums import PositionStatus
from shared.schemas import Position


class PositionRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def save_position(self, position: Position) -> PositionModel:
        query = select(PositionModel).where(PositionModel.position_id == position.position_id)
        res = await self.session.execute(query)
        existing = res.scalar_one_or_none()

        if existing:
            existing.current_price = position.current_price
            existing.unrealized_pnl = position.unrealized_pnl
            existing.realized_pnl = position.realized_pnl
            existing.status = (
                position.status.value if hasattr(position.status, "value") else str(position.status)
            )
            existing.closed_at = position.closed_at
            existing.fees_paid = position.fees_paid
            existing.updated_at = datetime.now(timezone.utc)
            return existing

        db_pos = PositionModel(
            position_id=position.position_id,
            symbol=position.symbol,
            side=position.side.value if hasattr(position.side, "value") else str(position.side),
            entry_price=position.entry_price,
            quantity=position.quantity,
            current_price=position.current_price,
            stop_loss=position.stop_loss,
            take_profit=position.take_profit,
            unrealized_pnl=position.unrealized_pnl,
            realized_pnl=position.realized_pnl,
            fees_paid=position.fees_paid,
            status=position.status.value
            if hasattr(position.status, "value")
            else str(position.status),
            strategy=position.strategy,
            opened_at=position.opened_at,
            closed_at=position.closed_at,
        )
        self.session.add(db_pos)
        return db_pos

    async def get_open_positions(self) -> List[PositionModel]:
        stmt = select(PositionModel).where(PositionModel.status == PositionStatus.OPEN.value)
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    async def get_all_positions(self, limit: int = 100) -> List[PositionModel]:
        stmt = select(PositionModel).order_by(PositionModel.created_at.desc()).limit(limit)
        res = await self.session.execute(stmt)
        return list(res.scalars().all())
