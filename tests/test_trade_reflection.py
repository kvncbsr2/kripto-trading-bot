import pytest

from core.llm.llm_provider import HeuristicFallbackProvider
from services.analytics.trade_reflection import TradeReflectionEngine, TradeReflectionRecord
from shared.enums import SignalDirection


@pytest.mark.asyncio
async def test_trade_reflection_loss():
    engine = TradeReflectionEngine(max_memory_size=10, llm_provider=HeuristicFallbackProvider())

    record = await engine.reflect_on_closed_trade(
        trade_id="trade-101",
        symbol="BTC/USDT",
        direction=SignalDirection.LONG,
        entry_price=65000.0,
        exit_price=64200.0,
        realized_pnl=-12.50,
        pnl_pct=-1.23,
        exit_reason="STOP_LOSS",
        duration_minutes=45.0,
    )

    assert isinstance(record, TradeReflectionRecord)
    assert record.realized_pnl == -12.50
    assert len(record.lesson_learned) > 0
    assert len(engine.memory) == 1

    # Test querying memory before next trade
    insight = engine.get_relevant_insights("BTC/USDT", SignalDirection.LONG)
    assert insight is not None
    assert "CAUTION" in insight


@pytest.mark.asyncio
async def test_trade_reflection_win():
    engine = TradeReflectionEngine(max_memory_size=10, llm_provider=HeuristicFallbackProvider())

    record = await engine.reflect_on_closed_trade(
        trade_id="trade-102",
        symbol="ETH/USDT",
        direction=SignalDirection.SHORT,
        entry_price=3500.0,
        exit_price=3400.0,
        realized_pnl=28.50,
        pnl_pct=2.85,
        exit_reason="TAKE_PROFIT",
        duration_minutes=90.0,
    )

    assert record.realized_pnl > 0
    recent = engine.get_recent_reflections(limit=5)
    assert len(recent) == 1
    assert recent[0]["symbol"] == "ETH/USDT"

    insight = engine.get_relevant_insights("ETH/USDT", SignalDirection.SHORT)
    assert insight is not None
    assert "PREVIOUS WIN" in insight
