from datetime import datetime
from typing import List, Optional

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models.tables import CandleModel
from shared.enums import Timeframe
from shared.schemas import Candle


class CandleRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def save_candle(self, candle: Candle) -> CandleModel:
        query = select(CandleModel).where(
            and_(
                CandleModel.symbol == candle.symbol,
                CandleModel.timeframe == candle.timeframe.value
                if isinstance(candle.timeframe, Timeframe)
                else candle.timeframe,
                CandleModel.timestamp == candle.timestamp,
            )
        )
        result = await self.session.execute(query)
        existing = result.scalar_one_or_none()

        if existing:
            existing.open = candle.open
            existing.high = candle.high
            existing.low = candle.low
            existing.close = candle.close
            existing.volume = candle.volume
            existing.quote_volume = candle.quote_volume
            return existing

        db_candle = CandleModel(
            exchange=candle.exchange.value
            if hasattr(candle.exchange, "value")
            else str(candle.exchange),
            symbol=candle.symbol,
            timeframe=candle.timeframe.value
            if hasattr(candle.timeframe, "value")
            else str(candle.timeframe),
            timestamp=candle.timestamp,
            open=candle.open,
            high=candle.high,
            low=candle.low,
            close=candle.close,
            volume=candle.volume,
            quote_volume=candle.quote_volume,
        )
        self.session.add(db_candle)
        return db_candle

    async def get_candles(
        self,
        symbol: str,
        timeframe: str,
        limit: int = 500,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
    ) -> List[CandleModel]:
        stmt = select(CandleModel).where(
            CandleModel.symbol == symbol, CandleModel.timeframe == timeframe
        )
        if start_time:
            stmt = stmt.where(CandleModel.timestamp >= start_time)
        if end_time:
            stmt = stmt.where(CandleModel.timestamp <= end_time)

        stmt = stmt.order_by(CandleModel.timestamp.desc()).limit(limit)
        result = await self.session.execute(stmt)
        candles = list(result.scalars().all())
        candles.reverse()  # Return in chronological order
        return candles
