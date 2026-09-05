from unittest.mock import AsyncMock, patch

import pytest

from services.market_data.dynamic_screener import DynamicUniverseScreener


@pytest.mark.asyncio
async def test_dynamic_screener_filters_stablecoins_and_volume():
    screener = DynamicUniverseScreener(min_volume_usd=10000000.0, max_symbols=10)

    mock_binance_data = [
        {"symbol": "BTCUSDT", "quoteVolume": "100000000.0", "bidPrice": "80000", "askPrice": "80001", "priceChangePercent": "-2.0"},
        {"symbol": "ETHUSDT", "quoteVolume": "50000000.0", "bidPrice": "2500", "askPrice": "2500.5", "priceChangePercent": "-1.5"},
        {"symbol": "USDCUSDT", "quoteVolume": "999999999.0", "bidPrice": "1.0", "askPrice": "1.0", "priceChangePercent": "0.0"},  # Should be EXCLUDED (Stablecoin)
        {"symbol": "BTCUPUSDT", "quoteVolume": "20000000.0", "bidPrice": "10.0", "askPrice": "10.1", "priceChangePercent": "5.0"},  # Should be EXCLUDED (Leveraged)
        {"symbol": "LOWVOLUSDT", "quoteVolume": "1000000.0", "bidPrice": "1.0", "askPrice": "1.01", "priceChangePercent": "-5.0"},  # Should be EXCLUDED (<$10M volume)
        {"symbol": "SOLUSDT", "quoteVolume": "30000000.0", "bidPrice": "100", "askPrice": "100.1", "priceChangePercent": "-3.0"},
        {"symbol": "DOGEUSDT", "quoteVolume": "15000000.0", "bidPrice": "0.08", "askPrice": "0.0801", "priceChangePercent": "-0.5"},
    ]

    with patch("httpx.AsyncClient.get") as mock_get:
        mock_resp = AsyncMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = mock_binance_data
        mock_get.return_value = mock_resp

        universe = await screener.get_liquid_universe()

        # Invariants:
        assert "BTC/USDT" in universe
        assert "ETH/USDT" in universe
        assert "SOL/USDT" in universe
        assert "DOGE/USDT" in universe

        # Stablecoins and leveraged tokens must NEVER enter trading universe
        assert "USDC/USDT" not in universe
        assert "USDCUSDT/USDT" not in universe
        assert "BTCUP/USDT" not in universe
        assert "LOWVOL/USDT" not in universe


@pytest.mark.asyncio
async def test_dynamic_screener_fallback_on_network_error():
    screener = DynamicUniverseScreener()

    with patch("httpx.AsyncClient.get", side_effect=Exception("Binance Network Timeout")):
        universe = await screener.get_liquid_universe()
        # Fallback to high-quality default universe without crashing
        assert len(universe) > 0
        assert "BTC/USDT" in universe
        assert "ETH/USDT" in universe
