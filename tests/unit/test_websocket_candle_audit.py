import json
import pytest
from datetime import datetime, timezone
from services.binance_connector.connector import BinanceConnector
from services.market_data.websocket.ws_client import BinanceWebSocketClient
from shared.enums import Timeframe
from shared.schemas import Candle


@pytest.mark.asyncio
async def test_open_candle_is_ignored():
    connector = BinanceConnector(symbols=["BTC/USDT"], timeframe=Timeframe.M15)
    received = []
    connector.add_candle_callback(lambda c: received.append(c))

    # Raw open candle (x=False)
    open_kline_msg = json.dumps({
        "stream": "btcusdt@kline_15m",
        "data": {
            "k": {
                "t": 1710000000000,
                "T": 1710000899999,
                "s": "BTCUSDT",
                "i": "15m",
                "o": "65000.0",
                "c": "65500.0",
                "h": "65600.0",
                "l": "64900.0",
                "v": "100.0",
                "x": False,
            }
        }
    })

    await connector._process_ws_message(open_kline_msg)
    assert len(received) == 0, "Open/forming candle (x=False) MUST NOT be emitted to candle callbacks"


@pytest.mark.asyncio
async def test_closed_candle_is_processed():
    connector = BinanceConnector(symbols=["BTC/USDT"], timeframe=Timeframe.M15)
    received = []
    connector.add_candle_callback(lambda c: received.append(c))

    # Raw closed candle (x=True)
    closed_kline_msg = json.dumps({
        "stream": "btcusdt@kline_15m",
        "data": {
            "k": {
                "t": 1710000000000,
                "T": 1710000899999,
                "s": "BTCUSDT",
                "i": "15m",
                "o": "65000.0",
                "c": "65500.0",
                "h": "65600.0",
                "l": "64900.0",
                "v": "100.0",
                "x": True,
            }
        }
    })

    await connector._process_ws_message(closed_kline_msg)
    assert len(received) == 1, "Closed candle (x=True) MUST be processed"
    assert received[0].symbol == "BTC/USDT"
    assert received[0].close == 65500.0
    assert received[0].is_closed is True


@pytest.mark.asyncio
async def test_closed_candle_duplicate_is_ignored():
    connector = BinanceConnector(symbols=["BTC/USDT"], timeframe=Timeframe.M15)
    received = []
    connector.add_candle_callback(lambda c: received.append(c))

    closed_kline_msg = json.dumps({
        "stream": "btcusdt@kline_15m",
        "data": {
            "k": {
                "t": 1710000000000,
                "T": 1710000899999,
                "s": "BTCUSDT",
                "i": "15m",
                "o": "65000.0",
                "c": "65500.0",
                "h": "65600.0",
                "l": "64900.0",
                "v": "100.0",
                "x": True,
            }
        }
    })

    # Send first time
    await connector._process_ws_message(closed_kline_msg)
    # Send second time (duplicate delivery)
    await connector._process_ws_message(closed_kline_msg)

    assert len(received) == 1, "Duplicate closed candle MUST be ignored"


@pytest.mark.asyncio
async def test_open_candle_cannot_change_signal():
    connector = BinanceConnector(symbols=["BTC/USDT"], timeframe=Timeframe.M15)
    received = []
    connector.add_candle_callback(lambda c: received.append(c))

    # Send 10 unclosed intermediate ticks with varying prices
    for i in range(10):
        msg = json.dumps({
            "stream": "btcusdt@kline_15m",
            "data": {
                "k": {
                    "t": 1710000000000,
                    "s": "BTCUSDT",
                    "i": "15m",
                    "o": "65000.0",
                    "c": f"{65000.0 + (i * 100)}",
                    "h": "66000.0",
                    "l": "64000.0",
                    "v": f"{i * 10}",
                    "x": False,
                }
            }
        })
        await connector._process_ws_message(msg)

    assert len(received) == 0, "No open intermediate ticks should leak to downstream strategy triggers"


def test_live_closed_candle_matches_backtest_input():
    candle = Candle(
        symbol="BTC/USDT",
        timeframe=Timeframe.M15,
        timestamp=datetime.now(timezone.utc),
        open=65000.0,
        high=65500.0,
        low=64800.0,
        close=65200.0,
        volume=150.0,
        is_closed=True,
    )
    assert isinstance(candle.open, float)
    assert isinstance(candle.high, float)
    assert isinstance(candle.low, float)
    assert isinstance(candle.close, float)
    assert isinstance(candle.volume, float)
    assert candle.is_closed is True


@pytest.mark.asyncio
async def test_ws_client_drops_open_and_duplicate_candles():
    client = BinanceWebSocketClient(symbols=["BTC/USDT"], timeframe=Timeframe.M1)
    received = []
    client.add_candle_listener(lambda c: received.append(c))

    # 1. Send forming candle
    open_msg = json.dumps({
        "data": {
            "k": {
                "t": 1710000000000,
                "s": "BTCUSDT",
                "o": "65000.0",
                "c": "65100.0",
                "h": "65200.0",
                "l": "64900.0",
                "v": "10.0",
                "q": "650000.0",
                "x": False,
            }
        }
    })
    await client._handle_message(open_msg)
    assert len(received) == 0, "ws_client must ignore open candle"

    # 2. Send closed candle
    closed_msg = json.dumps({
        "data": {
            "k": {
                "t": 1710000000000,
                "s": "BTCUSDT",
                "o": "65000.0",
                "c": "65100.0",
                "h": "65200.0",
                "l": "64900.0",
                "v": "10.0",
                "q": "650000.0",
                "x": True,
            }
        }
    })
    await client._handle_message(closed_msg)
    assert len(received) == 1, "ws_client must emit closed candle"

    # 3. Duplicate closed candle
    await client._handle_message(closed_msg)
    assert len(received) == 1, "ws_client must ignore duplicate closed candle"
