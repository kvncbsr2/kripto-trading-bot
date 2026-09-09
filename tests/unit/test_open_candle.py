import json
import pytest
from datetime import datetime, timezone
import pandas as pd
from services.binance_connector.connector import BinanceConnector
from services.strategy_engine.strategies.r10_rsi_divergence import R10RSIDivergenceStrategy
from shared.enums import Timeframe
from shared.schemas import Candle


@pytest.mark.asyncio
async def test_open_candle_cannot_trigger_strategy_signal():
    """
    BEFORE:
    open candle (x=False) was passed directly to callbacks -> strategy() evaluated mutating bar -> signal!

    AFTER (FIX):
    connector drops k.x == False -> strategy() never receives unclosed bar -> 0 signals generated!
    """
    connector = BinanceConnector(symbols=["BTC/USDT"], timeframe=Timeframe.M15)
    strategy = R10RSIDivergenceStrategy()

    received_candles = []
    generated_signals = []

    def on_candle(c: Candle):
        received_candles.append(c)
        # Mock dataframe feed to strategy
        df = pd.DataFrame([{
            "timestamp": c.timestamp,
            "open": c.open,
            "high": c.high,
            "low": c.low,
            "close": c.close,
            "volume": c.volume,
        }])
        sig = strategy.evaluate_from_dataframe(df, symbol=c.symbol)
        if sig:
            generated_signals.append(sig)

    connector.add_candle_callback(on_candle)

    # 1. Simulating 10 intermediate in-progress ticks of an UNCLOSED candle (x=False)
    for i in range(10):
        raw_ws_open_candle = json.dumps({
            "stream": "btcusdt@kline_15m",
            "data": {
                "k": {
                    "t": 1710000000000,
                    "T": 1710000899999,
                    "s": "BTCUSDT",
                    "i": "15m",
                    "o": "65000.0",
                    "c": f"{65000.0 + (i * 50.0)}",
                    "h": "66000.0",
                    "l": "64800.0",
                    "v": "100.0",
                    "x": False,  # Candle is still OPEN / MUTATING!
                }
            }
        })
        await connector._process_ws_message(raw_ws_open_candle)

    # Invariant: Zero open candles must reach strategy
    assert len(received_candles) == 0, "FIX VERIFIED: Open candles (x=False) are rejected by connector"
    assert len(generated_signals) == 0, "FIX VERIFIED: No premature signals generated on unclosed candles"


@pytest.mark.asyncio
async def test_closed_candle_is_processed_to_strategy():
    """
    AFTER (FIX):
    k.x == True -> connector processes closed candle -> callback receives verified bar.
    """
    connector = BinanceConnector(symbols=["BTC/USDT"], timeframe=Timeframe.M15)
    received_candles = []
    connector.add_candle_callback(lambda c: received_candles.append(c))

    raw_ws_closed_candle = json.dumps({
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
                "x": True,  # Candle is genuinely CLOSED!
            }
        }
    })

    await connector._process_ws_message(raw_ws_closed_candle)

    assert len(received_candles) == 1, "Closed candle (x=True) must be forwarded"
    assert received_candles[0].is_closed is True
    assert received_candles[0].close == 65500.0
