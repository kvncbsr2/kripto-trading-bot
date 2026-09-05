import time

import pytest

from services.market_data.market_data_service import MarketDataService


@pytest.mark.asyncio
async def test_stale_ticker_rejected_from_cache():
    mds = MarketDataService(symbols=["BTC/USDT"], max_ticker_age_seconds=5.0)

    # 1. Inject an artificially aged/stale ticker into cache (received 10 seconds ago)
    mds._ticker_cache["BTC/USDT"] = {
        "symbol": "BTC/USDT",
        "price": 60000.0,
        "bid": 59990.0,
        "ask": 60010.0,
        "spread": 20.0,
        "spread_bps": 3.3,
        "timestamp": "2026-09-05T00:00:00Z",
        "received_at": time.time() - 10.0,  # 10s old > 5s max age
        "source": "SIMULATED_WS",
    }

    # 2. Mock REST client to simulate network unreachable
    async def mock_fetch_ticker(sym):
        raise ConnectionError("REST unavailable")

    mds.connector.rest_client.fetch_ticker = mock_fetch_ticker

    # 3. get_live_ticker must REJECT the stale ticker and return None instead of serving stale data!
    result = await mds.get_live_ticker("BTC/USDT")
    assert result is None, "Stale ticker was returned when it should have been rejected (fail-closed)!"


@pytest.mark.asyncio
async def test_fresh_ticker_accepted_from_cache():
    mds = MarketDataService(symbols=["BTC/USDT"], max_ticker_age_seconds=10.0)

    # Fresh ticker (received 1 second ago)
    mds._ticker_cache["BTC/USDT"] = {
        "symbol": "BTC/USDT",
        "price": 65000.0,
        "bid": 64990.0,
        "ask": 65010.0,
        "spread": 20.0,
        "spread_bps": 3.0,
        "timestamp": "2026-09-05T00:00:00Z",
        "received_at": time.time() - 1.0,  # 1s old <= 10s max age
        "source": "BINANCE_REALTIME_WS",
    }

    result = await mds.get_live_ticker("BTC/USDT")
    assert result is not None
    assert result["price"] == 65000.0
