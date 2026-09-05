from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from apps.api.app.main import app
from shared.schemas import Candle


@pytest.mark.asyncio
async def test_agents_hub_debate_endpoint_unavailable():
    """Verifies fail-closed behavior when Binance market data is unavailable (Zero Fake Data)."""
    transport = ASGITransport(app=app)
    with patch("apps.api.app.api.state.market_data_service.get_live_ticker", AsyncMock(return_value=None)):
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.get("/api/v1/agents/debate", params={"symbol": "BTC/USDT"})
            assert res.status_code == 200
            data = res.json()
            assert data["status"] in ("unavailable", "success")
            assert data["data_quality"] == "UNAVAILABLE"


@pytest.mark.asyncio
async def test_agents_hub_debate_endpoint_success():
    """Verifies debate execution with valid market data."""
    transport = ASGITransport(app=app)
    mock_ticker = {"price": 65000.0, "bid": 64999.0, "ask": 65001.0}
    mock_orderbook = {"bid": 64999.0, "ask": 65001.0, "spread": 2.0, "spread_bps": 3.0}
    mock_candles = [
        Candle(
            symbol="BTC/USDT",
            timeframe="15m",
            timestamp=datetime.now(timezone.utc),
            open=64000.0 + i * 10,
            high=64100.0 + i * 10,
            low=63900.0 + i * 10,
            close=64050.0 + i * 10,
            volume=100.0,
            is_closed=True,
        )
        for i in range(60)
    ]

    with patch("apps.api.app.api.state.market_data_service.get_live_ticker", AsyncMock(return_value=mock_ticker)), \
         patch("apps.api.app.api.state.market_data_service.get_historical_klines", AsyncMock(return_value=mock_candles)), \
         patch("apps.api.app.api.state.market_data_service.get_orderbook", AsyncMock(return_value=mock_orderbook)), \
         patch("services.market_data.crypto_sentiment.sentiment_service.get_fear_and_greed_index", AsyncMock(return_value={"value": 50, "classification": "Neutral"})), \
         patch("services.market_data.binance_derivatives.derivatives_service.get_derivatives_metrics", AsyncMock(return_value={"funding_rate": 0.0001, "open_interest": 1000})):
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.get("/api/v1/agents/debate", params={"symbol": "BTC/USDT"})
            assert res.status_code == 200
            data = res.json()
            assert data["status"] == "success"
            assert data["data_quality"] == "REAL_BINANCE"
            assert "verdict" in data
            assert "bull_thesis" in data["verdict"]


@pytest.mark.asyncio
async def test_agents_hub_reflections_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/api/v1/analytics/reflections", params={"limit": 5})
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "success"
        assert "reflections" in data


@pytest.mark.asyncio
async def test_agents_hub_judge_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/api/v1/agents/judge")
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "success"
        assert "evaluation" in data
        assert "regime" in data["evaluation"]


@pytest.mark.asyncio
async def test_agents_hub_sentiment_metrics_endpoint():
    transport = ASGITransport(app=app)
    with patch("services.market_data.crypto_sentiment.sentiment_service.get_fear_and_greed_index", AsyncMock(return_value={"value": 60, "classification": "Greed"})), \
         patch("services.market_data.binance_derivatives.derivatives_service.get_derivatives_metrics", AsyncMock(return_value={"funding_rate": 0.0001, "long_short_ratio": 1.2})):
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            res = await ac.get("/api/v1/market/sentiment-metrics", params={"symbol": "BTC/USDT"})
            assert res.status_code == 200
            data = res.json()
            assert data["status"] == "success"
            assert "fear_and_greed" in data
            assert "derivatives" in data
