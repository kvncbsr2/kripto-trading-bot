from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models.tables import PortfolioSnapshotModel
from shared.schemas import PortfolioState


class PortfolioRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def save_snapshot(self, state: PortfolioState) -> PortfolioSnapshotModel:
        snapshot = PortfolioSnapshotModel(
            timestamp=state.timestamp or datetime.now(timezone.utc),
            balance=state.balance,
            equity=state.equity,
            unrealized_pnl=state.unrealized_pnl,
            realized_pnl=state.realized_pnl,
            daily_pnl=state.daily_pnl,
            max_drawdown=state.max_drawdown_current,
            open_positions_count=len(state.open_positions),
        )
        self.session.add(snapshot)
        return snapshot

    async def get_latest_snapshot(self) -> Optional[PortfolioSnapshotModel]:
        stmt = (
            select(PortfolioSnapshotModel)
            .order_by(PortfolioSnapshotModel.timestamp.desc())
            .limit(1)
        )
        res = await self.session.execute(stmt)
        return res.scalar_one_or_none()

    async def get_snapshots(self, limit: int = 100) -> List[PortfolioSnapshotModel]:
        stmt = (
            select(PortfolioSnapshotModel)
            .order_by(PortfolioSnapshotModel.timestamp.desc())
            .limit(limit)
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())
