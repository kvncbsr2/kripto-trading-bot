from typing import List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models.tables import SignalModel
from shared.schemas import Signal


class SignalRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def save_signal(self, signal: Signal) -> SignalModel:
        db_sig = SignalModel(
            symbol=signal.symbol,
            strategy=signal.strategy,
            direction=signal.direction.value
            if hasattr(signal.direction, "value")
            else str(signal.direction),
            entry_price=signal.entry_price,
            stop_price=signal.stop_price,
            take_profit=signal.take_profit,
            confidence=signal.confidence,
            regime=signal.regime.value if hasattr(signal.regime, "value") else str(signal.regime),
            reason=signal.reason,
            timestamp=signal.timestamp,
        )
        self.session.add(db_sig)
        return db_sig

    async def get_latest_signals(self, limit: int = 50) -> List[SignalModel]:
        stmt = select(SignalModel).order_by(SignalModel.timestamp.desc()).limit(limit)
        res = await self.session.execute(stmt)
        return list(res.scalars().all())
