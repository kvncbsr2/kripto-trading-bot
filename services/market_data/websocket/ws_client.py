import asyncio
import json
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

import websockets

from shared.enums import ExchangeName, Timeframe
from shared.logging import get_logger
from shared.schemas import Candle
from shared.utils import normalize_timestamp

logger = get_logger("websocket-client", service="market_data")


class BinanceWebSocketClient:
    """
    Production-grade WebSocket client for live Binance market data feeds.
    Includes auto-reconnect, heartbeat, duplicate detection, and subscriber routing.
    """

    WS_BASE_URL = "wss://stream.binance.com:9443/ws"

    def __init__(self, symbols: List[str], timeframe: Timeframe = Timeframe.M1):
        self.symbols = [s.lower().replace("/", "") for s in symbols]
        self.timeframe = timeframe
        self.callbacks: List[Callable[[Candle], None]] = []
        self._running = False
        self._last_candle_ts: Dict[str, datetime] = {}
        self._ws: Optional[Any] = None

    def add_candle_listener(self, callback: Callable[[Candle], None]):
        self.callbacks.append(callback)

    async def start(self):
        self._running = True
        reconnect_delay = 2
        max_reconnect_delay = 60

        while self._running:
            streams = "/".join([f"{s}@kline_{self.timeframe.value}" for s in self.symbols])
            url = f"wss://stream.binance.com:9443/stream?streams={streams}"
            try:
                logger.info(f"Connecting to Binance WebSocket: {url}")
                async with websockets.connect(url, ping_interval=20, ping_timeout=10) as ws:
                    self._ws = ws
                    reconnect_delay = 2  # reset on successful connect
                    logger.info("WebSocket connected successfully.")

                    while self._running:
                        message = await ws.recv()
                        await self._handle_message(message)

            except (websockets.ConnectionClosed, Exception) as e:
                if not self._running:
                    break
                logger.warning(
                    f"WebSocket disconnected ({e}). Reconnecting in {reconnect_delay}s..."
                )
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, max_reconnect_delay)

    async def _handle_message(self, raw_msg: Any):
        try:
            data = json.loads(raw_msg)
            if "data" in data and "k" in data["data"]:
                kline = data["data"]["k"]
                symbol_raw = kline["s"]  # e.g. BTCUSDT
                # Reformat to BTC/USDT
                symbol = (
                    f"{symbol_raw[:-4]}/{symbol_raw[-4:]}"
                    if symbol_raw.endswith("USDT")
                    else symbol_raw
                )

                # Strict anti-lookahead: Only emit closed candles (x=True)
                is_closed = bool(kline.get("x", False))
                if not is_closed:
                    return

                candle_ts = normalize_timestamp(kline["t"])
                # Out of order or duplicate check (timestamp must strictly advance)
                last_ts = self._last_candle_ts.get(symbol)
                if last_ts and candle_ts <= last_ts:
                    logger.debug(
                        f"Duplicate or out of order closed candle ignored for {symbol}: {candle_ts} <= {last_ts}"
                    )
                    return

                self._last_candle_ts[symbol] = candle_ts

                candle = Candle(
                    symbol=symbol,
                    timeframe=self.timeframe,
                    timestamp=candle_ts,
                    open=float(kline["o"]),
                    high=float(kline["h"]),
                    low=float(kline["l"]),
                    close=float(kline["c"]),
                    volume=float(kline["v"]),
                    quote_volume=float(kline["q"]),
                    exchange=ExchangeName.BINANCE,
                    is_closed=True,
                )


                for cb in self.callbacks:
                    if asyncio.iscoroutinefunction(cb):
                        await cb(candle)
                    else:
                        cb(candle)
        except Exception as e:
            logger.error(f"Error processing WS message: {e}")

    async def stop(self):
        self._running = False
        if self._ws:
            await self._ws.close()
        logger.info("WebSocket client stopped.")
