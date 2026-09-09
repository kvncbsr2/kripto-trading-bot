"""
Regression test for the open-candle repaint bug (2026-09 fix, re-applied).

Binance's klines/fetch_ohlcv endpoint includes the currently-forming candle as
the last element by default. Every causal strategy in this codebase (R10 in
particular, via its "Strict Causal Anti-Lookahead Guarantee") assumes the last
row of a fetched dataframe is a CLOSED bar. This test proves the connector now
strips a still-open last candle, and leaves a genuinely closed one untouched.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from services.binance_connector.connector import BinanceConnector


def _make_kline(ts: datetime, price: float = 100.0):
    ts_ms = int(ts.timestamp() * 1000)
    return [ts_ms, price, price + 1, price - 1, price, 1000.0]


@pytest.mark.asyncio
async def test_still_open_last_candle_is_dropped():
    connector = BinanceConnector(symbols=["BTC/USDT"])
    now = datetime.now(timezone.utc)

    closed = [_make_kline(now - timedelta(hours=6 - i)) for i in range(5)]
    still_forming = _make_kline(now - timedelta(minutes=2))
    raw = closed + [still_forming]

    connector.rest_client.fetch_ohlcv = AsyncMock(return_value=raw)

    candles = await connector.get_historical_candles("BTC/USDT", timeframe="1h", limit=6)

    assert len(candles) == 5, "The still-forming candle must be dropped"
    assert candles[-1].timestamp < now - timedelta(hours=1)


@pytest.mark.asyncio
async def test_genuinely_closed_last_candle_is_kept():
    connector = BinanceConnector(symbols=["BTC/USDT"])
    now = datetime.now(timezone.utc)

    closed = [_make_kline(now - timedelta(hours=5 - i) - timedelta(minutes=3)) for i in range(5)]
    connector.rest_client.fetch_ohlcv = AsyncMock(return_value=closed)

    candles = await connector.get_historical_candles("BTC/USDT", timeframe="1h", limit=5)

    assert len(candles) == 5, "A genuinely closed last candle must NOT be dropped"
