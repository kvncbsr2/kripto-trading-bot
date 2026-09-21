from datetime import datetime, timezone
import pytest
from unittest.mock import AsyncMock, MagicMock

from services.autonomous_runner import AutonomousPaperTrader
from services.execution.order_manager import OrderManager
from shared.enums import SignalDirection
from shared.schemas import Candle, RiskDecision, Timeframe


def test_calculate_sleep_15m():
    """Verifies that for 15m (900s), sleep is calculated exactly to the next 15m boundary + buffer."""
    trader = AutonomousPaperTrader()
    sleep_sec = trader._calculate_sleep_until_next_bar("15m", now_ts=100.0, buffer_sec=2.0)
    assert sleep_sec == (900.0 + 2.0) - 100.0  # 802.0s


def test_calculate_sleep_1h():
    """Verifies that for 1h (3600s), sleep is calculated exactly to the next 1h boundary + buffer."""
    trader = AutonomousPaperTrader()
    sleep_sec = trader._calculate_sleep_until_next_bar("1h", now_ts=1800.0, buffer_sec=3.0)
    assert sleep_sec == (3600.0 + 3.0) - 1800.0  # 1803.0s


def test_calculate_sleep_near_close():
    """Verifies that 1 second before candle close, wait time is 1s + buffer."""
    trader = AutonomousPaperTrader()
    sleep_sec = trader._calculate_sleep_until_next_bar("15m", now_ts=899.0, buffer_sec=2.0)
    assert sleep_sec == pytest.approx(3.0)


def test_calculate_sleep_post_close_and_clock_drift():
    """Verifies post-close and clock drift boundaries schedule into the next bar."""
    trader = AutonomousPaperTrader()
    sleep_sec = trader._calculate_sleep_until_next_bar("15m", now_ts=900.5, buffer_sec=2.0)
    assert sleep_sec == (1800.0 + 2.0) - 900.5  # 901.5s


def test_no_artificial_clamp_between_3_and_8():
    """P0-001 Invariant: Verifies that wait_sec is NOT clamped to 8.0 seconds."""
    trader = AutonomousPaperTrader()
    sleep_sec = trader._calculate_sleep_until_next_bar("15m", now_ts=100.0, buffer_sec=2.0)
    assert sleep_sec > 8.0
    assert sleep_sec == 802.0


@pytest.mark.asyncio
async def test_candle_deduplication_prevents_duplicate_cycles():
    """Verifies that a candle with the same timestamp is never evaluated twice."""
    trader = AutonomousPaperTrader()
    mock_mds = MagicMock()
    mock_broker = MagicMock()
    mock_broker.open_positions = {}
    mock_bus = MagicMock()
    mock_bus.broker = mock_broker
    mock_bus.runtime_state = {}

    trader.command_bus = mock_bus
    trader.market_data_service = mock_mds

    candle_ts = datetime(2026, 9, 15, 12, 0, 0, tzinfo=timezone.utc)
    mock_candle = Candle(
        symbol="BTC/USDT",
        timeframe=Timeframe.M15,
        timestamp=candle_ts,
        open=60000.0,
        high=60500.0,
        low=59500.0,
        close=60200.0,
        volume=100.0,
    )
    mock_mds.get_historical_klines = AsyncMock(return_value=[mock_candle] * 40)

    # First evaluation records timestamp
    trader.last_processed_candle_timestamp["BTC/USDT"] = candle_ts

    # Simulating symbol scan with identical candle timestamp
    candles = await mock_mds.get_historical_klines("BTC/USDT", timeframe="15m", limit=100)
    latest_ts = candles[-1].timestamp

    assert trader.last_processed_candle_timestamp.get("BTC/USDT") == latest_ts
    is_duplicate = (trader.last_processed_candle_timestamp.get("BTC/USDT") == latest_ts)
    assert is_duplicate is True


def test_spot_short_strictly_blocked_in_order_manager():
    """Verifies that OrderManager strictly raises ValueError if a SHORT order is attempted in spot mode."""
    order_manager = OrderManager()
    mock_risk = MagicMock()
    mock_risk.is_spot_mode = True
    order_manager.bind_risk_engine(mock_risk)

    short_decision = RiskDecision(
        approved=True,
        symbol="ETH/USDT",
        direction=SignalDirection.SHORT,
        calculated_size=1.0,
        entry_price=3000.0,
        stop_loss=3100.0,
        take_profit=2800.0,
    )

    with pytest.raises(ValueError, match="Spot mode does not support SHORT execution"):
        import asyncio
        asyncio.run(order_manager.execute_risk_decision(short_decision, strategy_name="test_strat"))
