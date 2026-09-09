import pytest
import asyncio
from services.intelligence.whale_tracker import WhaleRadarTracker, WhaleTrade

@pytest.mark.asyncio
async def test_whale_tracker_manual_trades():
    tracker = WhaleRadarTracker(min_usd=50000.0, max_buffer=20)
    
    # Add a normal whale buy
    t1 = WhaleTrade(
        trade_id="101",
        symbol="BTCUSDT",
        price=65000.0,
        quantity=1.5,
        total_usd=97500.0,
        side="BUY",
        timestamp="14:00:00",
        timestamp_ms=1700000000000,
        is_mega=False,
        is_super=False
    )
    tracker.add_trade(t1)

    # Add a mega whale sell
    t2 = WhaleTrade(
        trade_id="102",
        symbol="ETHUSDT",
        price=3500.0,
        quantity=100.0,
        total_usd=350000.0,
        side="SELL",
        timestamp="14:01:00",
        timestamp_ms=1700000060000,
        is_mega=True,
        is_super=False
    )
    tracker.add_trade(t2)

    recent = await tracker.get_recent_trades(limit=10)
    assert len(recent) == 2
    assert recent[0]["trade_id"] == "102"  # Newest first
    assert recent[0]["is_mega"] is True

    flow = await tracker.get_flow_summary()
    assert flow["total_trades_tracked"] == 2
    assert flow["total_buy_usd"] == 97500.0
    assert flow["total_sell_usd"] == 350000.0
    assert flow["net_flow_usd"] == round(97500.0 - 350000.0, 2)
    assert flow["mega_trades_count"] == 1
    assert len(flow["symbols"]) == 2

@pytest.mark.asyncio
async def test_whale_tracker_seed_live():
    tracker = WhaleRadarTracker(symbols=["BTCUSDT", "ETHUSDT"], min_usd=30000.0)
    try:
        await tracker.seed_historical_whales()
        recent = await tracker.get_recent_trades(limit=5)
        # Even if market is quiet or active, list is returned cleanly
        assert isinstance(recent, list)
        summary = await tracker.get_flow_summary()
        assert "buy_ratio_pct" in summary
        assert "sentiment" in summary
    finally:
        await tracker.stop()
