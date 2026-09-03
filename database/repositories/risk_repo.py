from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.models.tables import RiskEventModel


class RiskRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def log_event(
        self,
        event_type: str,
        description: str,
        symbol: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> RiskEventModel:
        event = RiskEventModel(
            timestamp=datetime.now(timezone.utc),
            event_type=event_type,
            symbol=symbol,
            description=description,
            metadata_json=metadata,
        )
        self.session.add(event)
        return event

    async def get_events(self, limit: int = 50) -> List[RiskEventModel]:
        stmt = select(RiskEventModel).order_by(RiskEventModel.timestamp.desc()).limit(limit)
        res = await self.session.execute(stmt)
        return list(res.scalars().all())
