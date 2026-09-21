import os
import sqlite3
import tempfile
import pytest

from services.risk_engine.risk_engine import RiskEngine
from database.repositories.risk_repo import RiskRepository
from shared.enums import SignalDirection
from shared.schemas import PortfolioState, Signal

def test_daily_loss_limit_trips_and_writes_to_risk_events(monkeypatch):
    """Simulate daily loss limit breach and verify rows are persisted to risk_events table."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tf:
        db_path = tf.name

    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS risk_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME NOT NULL,
            event_type VARCHAR(50) NOT NULL,
            symbol VARCHAR(20),
            description TEXT NOT NULL,
            metadata_json JSON,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL
        );
    """)
    conn.commit()
    conn.close()

    RiskRepository._THROTTLE_CACHE.clear()
    orig_record = RiskRepository.record_event_sync

    # Pass db_path into record_event_sync via monkeypatch wrapper
    monkeypatch.setattr(
        RiskRepository,
        "record_event_sync",
        lambda event_type, description, symbol=None, metadata=None, _=None, **kwargs: orig_record(
            event_type, description, symbol, metadata, db_path=db_path, throttle_seconds=0.0
        )
    )

    # Isolate from DPO preference gate state pollution from earlier tests
    from unittest.mock import MagicMock
    from services.learning.dpo_signal_gate import dpo_signal_gate
    monkeypatch.setattr(dpo_signal_gate, "evaluate_signal", lambda *args, **kwargs: MagicMock(approved=True))

    engine = RiskEngine(daily_max_loss_usd=50.0, max_open_positions=2)
    # Simulate a portfolio that has breached daily loss limit
    portfolio = PortfolioState(
        balance=9800.0,
        equity=9800.0,
        daily_pnl=-65.0,  # Breaches 50.0 daily loss limit!
    )

    sig = Signal(
        symbol="BTC/USDT",
        strategy="bollinger_volume_breakout",
        direction=SignalDirection.LONG,
        entry_price=70000.0,
        stop_price=68000.0,
        take_profit=74000.0,
        confidence=0.90,
    )

    decision = engine.evaluate_signal(sig, portfolio)

    # 1. Assert signal was rejected due to daily loss limit
    assert not decision.approved
    assert "DAILY_RISK_LOCK" in decision.reason or "Daily loss limit exceeded" in decision.reason

    # 2. Query the SQLite risk_events table and verify real persistence
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("SELECT event_type, symbol, description, metadata_json FROM risk_events")
    rows = cur.fetchall()
    conn.close()

    assert len(rows) >= 1, f"Expected at least 1 risk event recorded, found {len(rows)}"
    event_types = [r[0] for r in rows]
    assert any(et in ("DAILY_LOSS_EXCEEDED", "DAILY_RISK_LOCK") for et in event_types)

    try:
        os.remove(db_path)
    except Exception:
        pass
