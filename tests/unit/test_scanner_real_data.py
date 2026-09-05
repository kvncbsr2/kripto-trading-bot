import pytest
from unittest.mock import AsyncMock

from services.market_scanner.scanner import MarketScanner


@pytest.mark.asyncio
async def test_market_scanner_deterministic_scoring_with_real_data():
    """Verifies scanner produces deterministic opportunity scores with zero random injection (AUDIT-03)."""
    scanner = MarketScanner()

    mock_mds = AsyncMock()
    mock_mds.get_live_ticker.return_value = {
        "symbol": "BTC/USDT",
        "price": 60000.0,
        "bid": 59990.0,
        "ask": 60010.0,
        "volume_24h": 50000000.0,
        "spread_bps": 3.33,
        "high_24h": 61000.0,
        "low_24h": 59000.0,
    }

    # Run scanner twice with identical input
    res1 = await scanner.scan_market(market_data_service=mock_mds)
    score1 = next(s.opportunity_score for s in res1 if s.symbol == "BTC/USDT")

    res2 = await scanner.scan_market(market_data_service=mock_mds)
    score2 = next(s.opportunity_score for s in res2 if s.symbol == "BTC/USDT")

    # Determinism assertion: scores must be identical
    assert score1 == score2
    assert score1 > 0.0
    assert next(s.trade_allowed for s in res1 if s.symbol == "BTC/USDT") is True
