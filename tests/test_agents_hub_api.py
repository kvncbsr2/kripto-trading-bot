import pytest
from httpx import ASGITransport, AsyncClient
from apps.api.app.main import app


@pytest.mark.asyncio
async def test_agents_hub_debate_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/api/v1/agents/debate", params={"symbol": "BTC/USDT"})
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "success"
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
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/api/v1/market/sentiment-metrics", params={"symbol": "BTC/USDT"})
        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "success"
        assert "fear_and_greed" in data
        assert "derivatives" in data
