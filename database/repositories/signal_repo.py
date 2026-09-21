import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from database.models.tables import SignalModel
from shared.schemas import Signal

DB_PATH = Path(__file__).resolve().parents[2] / "kripto_agent.db"


class SignalRepository:
    def __init__(self, session: Optional[AsyncSession] = None, db_path: Optional[str] = None):
        self.session = session
        self.db_path = str(db_path or DB_PATH)

    async def save_signal(self, signal: Signal) -> SignalModel:
        if not self.session:
            raise RuntimeError("AsyncSession required for save_signal")
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
        if not self.session:
            raise RuntimeError("AsyncSession required for get_latest_signals")
        stmt = select(SignalModel).order_by(SignalModel.timestamp.desc()).limit(limit)
        res = await self.session.execute(stmt)
        return list(res.scalars().all())

    @classmethod
    def record_signal_sync(
        cls,
        symbol: str,
        strategy_id: str,
        direction: str,
        entry_price: float,
        stop_price: float,
        take_profit: float,
        confidence: float,
        regime: str,
        signal_id: Optional[str] = None,
        risk_profile_level: Optional[int] = None,
        signal_score: Optional[float] = None,
        opportunity_score: Optional[float] = None,
        approved: bool = False,
        rejection_reason: Optional[str] = None,
        regime_state: Optional[str] = None,
        timestamp: Optional[datetime] = None,
        db_path: Optional[str] = None,
    ) -> Optional[int]:
        """Synchronously records an evaluated signal into SQLite signals table."""
        path = db_path or str(DB_PATH)
        ts = timestamp or datetime.now(timezone.utc)
        now_str = ts.isoformat()
        try:
            with sqlite3.connect(path, timeout=10.0) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO signals (
                        signal_id, symbol, strategy, strategy_id, direction,
                        entry_price, stop_price, take_profit, confidence,
                        regime, regime_state, risk_profile_level,
                        signal_score, opportunity_score, approved, rejection_reason,
                        timestamp, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        signal_id,
                        symbol,
                        strategy_id,
                        strategy_id,
                        direction,
                        entry_price,
                        stop_price,
                        take_profit,
                        confidence,
                        regime,
                        regime_state,
                        risk_profile_level,
                        signal_score,
                        opportunity_score,
                        1 if approved else 0,
                        rejection_reason,
                        now_str,
                        now_str,
                        now_str,
                    ),
                )
                conn.commit()
                return cursor.lastrowid
        except Exception as e:
            # Non-blocking on database failure
            return None

    @classmethod
    def update_signal_outcome_sync(
        cls,
        signal_id: Optional[str] = None,
        symbol: Optional[str] = None,
        realized_pnl: float = 0.0,
        r_multiple: float = 0.0,
        exit_reason: Optional[str] = None,
        closed_at: Optional[str] = None,
        db_path: Optional[str] = None,
    ) -> bool:
        """Synchronously updates outcome fields in signals table upon position closure."""
        path = db_path or str(DB_PATH)
        now_str = closed_at or datetime.now(timezone.utc).isoformat()
        try:
            with sqlite3.connect(path, timeout=10.0) as conn:
                cursor = conn.cursor()
                if signal_id:
                    cursor.execute(
                        """
                        UPDATE signals
                        SET outcome_realized_pnl = ?,
                            outcome_r_multiple = ?,
                            outcome_exit_reason = ?,
                            outcome_closed_at = ?,
                            updated_at = ?
                        WHERE signal_id = ? OR id = ?
                        """,
                        (realized_pnl, r_multiple, exit_reason, now_str, now_str, signal_id, signal_id),
                    )
                elif symbol:
                    cursor.execute(
                        """
                        UPDATE signals
                        SET outcome_realized_pnl = ?,
                            outcome_r_multiple = ?,
                            outcome_exit_reason = ?,
                            outcome_closed_at = ?,
                            updated_at = ?
                        WHERE id = (
                            SELECT id FROM signals
                            WHERE symbol = ? AND approved = 1 AND outcome_realized_pnl IS NULL
                            ORDER BY timestamp DESC LIMIT 1
                        )
                        """,
                        (realized_pnl, r_multiple, exit_reason, now_str, now_str, symbol),
                    )
                conn.commit()
                return cursor.rowcount > 0
        except Exception:
            return False

