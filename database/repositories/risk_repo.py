from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

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

    _THROTTLE_CACHE: Dict[Tuple[str, Optional[str]], float] = {}
    _THROTTLE_LOCK = None

    @classmethod
    def record_event_sync(
        cls,
        event_type: str,
        description: str,
        symbol: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
        db_path: Optional[str] = None,
        throttle_seconds: float = 300.0,
    ) -> bool:
        """
        Synchronously persists a risk event to the SQLite risk_events table.
        Deduplicates identical (event_type, symbol) occurrences within throttle_seconds (default 5m)
        to prevent database spam while maintaining audit fidelity.
        """
        import json
        import sqlite3
        import time
        from pathlib import Path

        now_ts = time.time()
        throttle_key = (event_type, symbol)
        last_logged = cls._THROTTLE_CACHE.get(throttle_key, 0.0)
        if (now_ts - last_logged) < throttle_seconds:
            return True  # Throttled, avoid database bloat

        cls._THROTTLE_CACHE[throttle_key] = now_ts
        target_db = str(db_path or Path(__file__).resolve().parents[2] / "kripto_agent.db")
        now_iso = datetime.now(timezone.utc).isoformat()
        meta_json = json.dumps(metadata) if metadata else None

        try:
            with sqlite3.connect(target_db, timeout=10.0) as conn:
                conn.execute(
                    """
                    INSERT INTO risk_events (timestamp, event_type, symbol, description, metadata_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (now_iso, event_type, symbol, description, meta_json, now_iso, now_iso),
                )
                conn.commit()
            return True
        except Exception as e:
            import logging
            logging.getLogger("risk-repo").warning(f"Failed to record risk event synchronously: {e}")
            return False
