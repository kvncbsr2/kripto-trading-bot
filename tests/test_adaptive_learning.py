import pytest
import sqlite3
import tempfile
from datetime import datetime, timezone

from core.llm.llm_provider import HeuristicFallbackProvider
from services.analytics.trade_reflection import (
    TradeReflectionEngine,
    TradeReflectionRecord,
)
from services.strategy_engine.adaptive_learning import AdaptiveLearningEngine
from shared.enums import PositionSide, PositionStatus, SignalDirection
from shared.schemas import Position


@pytest.fixture
def temp_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    yield db_path


@pytest.mark.asyncio
async def test_causal_diagnostics_premature_stop_wick(temp_db):
    engine = TradeReflectionEngine(
        max_memory_size=10,
        llm_provider=HeuristicFallbackProvider(),
        db_path=temp_db,
    )

    # Simulated trade: Entered at 100, went up to 102 (+2% peak), but got wicked out at 99
    record = await engine.reflect_on_closed_trade(
        trade_id="trade-wick-1",
        symbol="SOL/USDT",
        direction=SignalDirection.LONG,
        entry_price=100.0,
        exit_price=99.0,
        realized_pnl=-1.50,
        pnl_pct=-1.0,
        exit_reason="STOP_LOSS",
        duration_minutes=35.0,
        peak_price=102.0,
    )

    assert isinstance(record, TradeReflectionRecord)
    assert record.category == "LOSS_PREMATURE_STOP_WICK"
    assert "iğne" in record.root_cause or "ATR" in record.recommendation
    assert record.adaptive_adjustment.get("atr_multiplier_adj") == 0.2

    # Verify SQLite persistence
    conn = sqlite3.connect(temp_db)
    row = conn.execute("SELECT count(*) FROM trade_reflections WHERE symbol='SOL/USDT'").fetchone()
    assert row[0] == 1
    conn.close()


@pytest.mark.asyncio
async def test_causal_diagnostics_momentum_exhaustion(temp_db):
    engine = TradeReflectionEngine(
        max_memory_size=10,
        llm_provider=HeuristicFallbackProvider(),
        db_path=temp_db,
    )

    # Simulated trade: Reversed immediately within 8 minutes
    record = await engine.reflect_on_closed_trade(
        trade_id="trade-mom-1",
        symbol="BTC/USDT",
        direction=SignalDirection.LONG,
        entry_price=70000.0,
        exit_price=69200.0,
        realized_pnl=-12.0,
        pnl_pct=-1.14,
        exit_reason="STOP_LOSS",
        duration_minutes=8.0,
        peak_price=70050.0,
    )

    assert record.category == "LOSS_MOMENTUM_EXHAUSTION"
    assert record.adaptive_adjustment.get("rsi_entry_delta") == -2.0


def test_adaptive_learning_cooldown_and_parameters(temp_db):
    learner = AdaptiveLearningEngine(db_path=temp_db)

    # Initial state
    assert learner.dynamic_atr_multiplier == 1.20
    assert learner.dynamic_min_opportunity_score == 50.0

    # 1. Simulate a loss on NEAR/USDT
    dummy_pos = Position(
        position_id="pos_near_1",
        symbol="NEAR/USDT",
        side=PositionSide.LONG,
        entry_price=2.50,
        current_price=2.45,
        quantity=200.0,
        realized_pnl=-10.0,
        unrealized_pnl=0.0,
        status=PositionStatus.CLOSED,
        stop_loss=2.45,
        take_profit=2.65,
        fees_paid=0.20,
        opened_at=datetime.now(timezone.utc),
        closed_at=datetime.now(timezone.utc),
        peak_price=2.52,  # +0.8% peak before drop
    )

    learner.record_closed_trade(dummy_pos, exit_reason="STOP_LOSS", db_path=temp_db)

    # Should trigger symbol cooldown
    in_cd, reason, rem_sec = learner.is_symbol_in_cooldown("NEAR/USDT")
    assert in_cd is True
    assert rem_sec > 800  # At least ~15 mins (900s)
    assert learner.symbol_metrics["NEAR/USDT"]["losses"] == 1
    assert learner.symbol_metrics["NEAR/USDT"]["consecutive_losses"] == 1

    # Should have adapted ATR stop multiplier because of premature stop
    assert learner.dynamic_atr_multiplier > 1.20

    # Confidence for NEAR should now carry penalty
    adapted_conf = learner.get_adapted_confidence("NEAR/USDT", base_confidence=0.85)
    assert adapted_conf < 0.85

    # 2. Simulate subsequent win
    dummy_pos_win = Position(
        position_id="pos_near_2",
        symbol="NEAR/USDT",
        side=PositionSide.LONG,
        entry_price=2.45,
        current_price=2.55,
        quantity=200.0,
        realized_pnl=20.0,
        unrealized_pnl=0.0,
        status=PositionStatus.CLOSED,
        stop_loss=2.40,
        take_profit=2.55,
        fees_paid=0.20,
        opened_at=datetime.now(timezone.utc),
        closed_at=datetime.now(timezone.utc),
        peak_price=2.55,
    )

    learner.record_closed_trade(dummy_pos_win, exit_reason="TAKE_PROFIT", db_path=temp_db)

    # Win resets consecutive losses and clears cooldown
    in_cd2, _, _ = learner.is_symbol_in_cooldown("NEAR/USDT")
    assert in_cd2 is False
    assert learner.symbol_metrics["NEAR/USDT"]["consecutive_losses"] == 0

    # Verify state persistence & restoration
    learner2 = AdaptiveLearningEngine(db_path=temp_db)
    assert learner2.total_trades_analyzed == 2
    assert learner2.total_wins == 1
    assert learner2.total_losses == 1
