import pytest
from services.market_data.crypto_sentiment import CryptoSentimentService
from services.market_data.binance_derivatives import BinanceDerivativesService


@pytest.mark.asyncio
async def test_crypto_sentiment_fallback_and_scoring():
    service = CryptoSentimentService(cache_ttl_seconds=100)
    res = await service.get_fear_and_greed_index()
    assert "value" in res
    assert "classification" in res
    assert -1.0 <= res["normalized_score"] <= 1.0


@pytest.mark.asyncio
async def test_binance_derivatives_formatting_and_structure():
    service = BinanceDerivativesService(cache_ttl_seconds=60)
    assert service._format_symbol("BTC/USDT") == "BTCUSDT"
    assert service._format_symbol("eth/usdt") == "ETHUSDT"

    # Test query returns valid keys
    res = await service.get_derivatives_metrics("BTC/USDT")
    assert "symbol" in res
    assert "long_short_ratio" in res
    assert "taker_buy_sell_ratio" in res
