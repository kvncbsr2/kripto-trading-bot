from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
import pytest

from services.market_data.market_data_service import MarketDataService
from shared.enums import Timeframe
from shared.schemas import Candle


@pytest.mark.asyncio
async def test_market_data_service_candle_caching():
    mock_connector = MagicMock()
    now = datetime.now(timezone.utc)
    sample_candles = [
        Candle(
            symbol="BTC/USDT",
            timeframe=Timeframe.M15,
            timestamp=now,
            open=60000.0,
            high=60100.0,
            low=59900.0,
            close=60050.0,
            volume=100.0,
        )
        for _ in range(50)
    ]
    mock_connector.get_historical_candles = AsyncMock(return_value=sample_candles)
    mock_connector.add_candle_callback = MagicMock()
    mock_connector.add_book_ticker_callback = MagicMock()

    mds = MarketDataService(symbols=["BTC/USDT"], connector=mock_connector)

    # Call 1: Cache miss -> queries connector
    c1 = await mds.get_historical_klines("BTC/USDT", timeframe="15m", limit=50)
    assert len(c1) == 50
    assert mock_connector.get_historical_candles.call_count == 1

    # Call 2: Same 15m bucket -> Cache hit! Does not query connector again
    c2 = await mds.get_historical_klines("BTC/USDT", timeframe="15m", limit=50)
    assert len(c2) == 50
    assert mock_connector.get_historical_candles.call_count == 1

    # Call 3: force_refresh=True -> queries connector again
    c3 = await mds.get_historical_klines("BTC/USDT", timeframe="15m", limit=50, force_refresh=True)
    assert len(c3) == 50
    assert mock_connector.get_historical_candles.call_count == 2
