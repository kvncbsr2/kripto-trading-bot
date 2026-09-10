import pytest
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from httpx import ASGITransport, AsyncClient

from apps.api.app.main import app
from services.market_data.market_data_service import MarketDataService
from shared.config import get_settings
from shared.schemas import Candle, Timeframe


@pytest.mark.asyncio
async def test_dashboard_does_not_leak_raw_api_secret_and_uses_httponly_cookie():
    """
    P0 Security Test:
    1. Dashboard HTML response must NOT contain plain-text injected window.__KRIPTO_API_KEY__.
    2. Session cookie 'kripto_admin_token' must have httpOnly=True to prevent XSS theft.
    """
    transport = ASGITransport(app=app)
    settings = get_settings()

    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/dashboard")
        assert res.status_code == 200

        # Verify no raw secret is exposed in client-side HTML
        assert "window.__KRIPTO_API_KEY__" not in res.text
        assert settings.API_ADMIN_KEY not in res.text

        # Verify cookie is httpOnly
        cookie_header = res.headers.get("set-cookie", "")
        assert "kripto_admin_token=" in cookie_header
        assert "httponly" in cookie_header.lower()


@pytest.mark.asyncio
async def test_websocket_first_kline_cache_avoids_redundant_rest_polling():
    """
    P0 Rate-Limit & Microstructure Test:
    Verifies that when in-memory kline cache is fresh (within 2 timeframe intervals),
    get_historical_candles serves directly from RAM without calling REST fetch_ohlcv.
    """
    mds = MarketDataService()
    mds.connector = MagicMock()
    mds.connector.get_historical_candles = AsyncMock()

    # 1. Populate cache with 50 fresh 15m candles
    now = datetime.now(timezone.utc)
    mock_candles = [
        Candle(
            symbol="BTC/USDT",
            timeframe=Timeframe.M15,
            timestamp=now - timedelta(minutes=15 * (50 - i)),
            open=50000.0 + i,
            high=50100.0 + i,
            low=49900.0 + i,
            close=50050.0 + i,
            volume=100.0,
            is_closed=True,
        )
        for i in range(50)
    ]
    mds._candle_cache["BTC/USDT"] = {"15m": mock_candles}

    # 2. Query historical klines for BTC/USDT
    result = await mds.get_historical_candles("BTC/USDT", timeframe="15m", limit=30)

    # 3. Must be served directly from RAM (0 REST calls made)
    assert len(result) == 30
    assert result[-1].close == mock_candles[-1].close
    mds.connector.get_historical_candles.assert_not_called()


@pytest.mark.asyncio
async def test_warm_up_cache_prepopulates_local_memory_and_enables_zero_rest_queries():
    """
    Verifies that warm_up_cache successfully pre-populates local RAM cache from REST,
    so subsequent live queries are served at zero REST cost.
    """
    mds = MarketDataService()
    now = datetime.now(timezone.utc)
    mock_candles = [
        Candle(
            symbol="ETH/USDT",
            timeframe=Timeframe.M15,
            timestamp=now - timedelta(minutes=15 * (35 - i)),
            open=3000.0 + i,
            high=3050.0 + i,
            low=2990.0 + i,
            close=3020.0 + i,
            volume=50.0,
            is_closed=True,
        )
        for i in range(35)
    ]

    mock_connector = MagicMock()
    mock_connector.symbols = ["ETH/USDT"]
    mock_connector.get_historical_candles = AsyncMock(return_value=mock_candles)
    mds.connector = mock_connector

    # Warm up
    count = await mds.warm_up_cache(symbols=["ETH/USDT"], timeframe="15m", limit=35)
    assert count == 1
    assert mock_connector.get_historical_candles.call_count == 1

    # Reset mock call count to verify subsequent queries
    mock_connector.get_historical_candles.reset_mock()

    # Second query for ETH/USDT must hit RAM cache with zero REST calls!
    cached_res = await mds.get_historical_candles("ETH/USDT", timeframe="15m", limit=30)
    assert len(cached_res) == 30
    mock_connector.get_historical_candles.assert_not_called()
