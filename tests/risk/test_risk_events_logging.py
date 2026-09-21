import sqlite3
import tempfile
from datetime import datetime, timezone
import pytest

from services.risk_engine.risk_engine import RiskEngine
from services.risk_engine.circuit_breaker import CircuitBreaker
from database.repositories.risk_repo import RiskRepository
from shared.enums import SignalDirection
from shared.schemas import PortfolioState, Signal

@pytest.fixture
def temp_db():
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
    yield db_path
    try:
        os.remove(db_path)
    except Exception:
        pass

def test_risk_events_record_sync(temp_db):
    success = RiskRepository.record_event_sync(
        event_type="TEST_RISK_LOCK",
        description="Daily risk lock test event",
        symbol="BTC/USDT",
        metadata={"loss": 60.0},
        db_path=temp_db,
    )
    assert success is True

    conn = sqlite3.connect(temp_db)
    cur = conn.cursor()
    cur.execute("SELECT event_type, symbol, description FROM risk_events")
    row = cur.fetchone()
    conn.close()

    assert row is not None
    assert row[0] == "TEST_RISK_LOCK"
    assert row[1] == "BTC/USDT"
    assert "Daily risk lock" in row[2]

def test_risk_engine_evaluate_signal_logs_to_risk_events(monkeypatch):
    engine = RiskEngine(max_open_positions=0)
    portfolio = PortfolioState(balance=5000.0, equity=5000.0)
    sig = Signal(
        symbol="ETH/USDT",
        strategy="bollinger_volume_breakout",
        direction=SignalDirection.LONG,
        entry_price=2500.0,
        stop_price=2450.0,
        take_profit=2600.0,
        confidence=0.85,
    )

    logged_events = []
    def mock_record_sync(event_type, description, symbol=None, metadata=None, db_path=None):
        logged_events.append((event_type, symbol, description))
        return True

    monkeypatch.setattr(RiskRepository, "record_event_sync", mock_record_sync)

    decision = engine.evaluate_signal(sig, portfolio)
    assert not decision.approved
    assert len(logged_events) >= 1
    assert logged_events[0][1] == "ETH/USDT"
