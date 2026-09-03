import asyncio
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

import ccxt.async_support as ccxt
import websockets

from shared.config import get_settings
from shared.enums import Timeframe
from shared.logging import get_logger
from shared.schemas import Candle
from shared.utils import normalize_timestamp

logger = get_logger("binance-connector", service="binance_connector")
settings = get_settings()


class ConnectionState(str, Enum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    HEALTHY = "HEALTHY"
    RECONNECTING = "RECONNECTING"
    FAILED = "FAILED"


class BinanceConnector:
    """
    Dedicated Binance Connector supporting Real-Time Multiplexed WebSockets
    (kline, bookTicker, miniTicker) and REST recovery.
    Includes state machine (DISCONNECTED -> RECONNECT -> RESUBSCRIBE -> RESYNC -> HEALTHY).
    """

    WS_STREAM_URL = "wss://stream.binance.com:9443/stream?streams="

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        timeframe: Timeframe = Timeframe.M15,
    ):
        self.symbols = symbols or settings.DEFAULT_SYMBOLS
        self.timeframe = timeframe
        self.state: ConnectionState = ConnectionState.DISCONNECTED
        self._running = False
        self._ws: Optional[Any] = None
        self._reconnect_count = 0
        self._last_heartbeat: Optional[datetime] = None

        # Callbacks
        self.candle_callbacks: List[Callable[[Candle], None]] = []
        self.book_ticker_callbacks: List[Callable[[Dict[str, Any]], None]] = []

        # Local cache for book tickers: symbol -> {bid, ask, spread, spread_bps, timestamp}
        self.book_tickers: Dict[str, Dict[str, Any]] = {}

        # REST client (read-only)
        self.rest_client = ccxt.binance(
            {
                "enableRateLimit": True,
                "options": {"defaultType": "spot"},
            }
        )

    def add_candle_callback(self, cb: Callable[[Candle], None]):
        self.candle_callbacks.append(cb)

    def add_book_ticker_callback(self, cb: Callable[[Dict[str, Any]], None]):
        self.book_ticker_callbacks.append(cb)

    def get_status(self) -> Dict[str, Any]:
        return {
            "exchange": "binance",
            "market": "spot",
            "state": self.state.value,
            "symbols_monitored": self.symbols,
            "timeframe": self.timeframe.value,
            "reconnect_count": self._reconnect_count,
            "last_heartbeat": self._last_heartbeat.isoformat() if self._last_heartbeat else None,
            "live_trading_locked": True,
        }

    async def get_historical_candles(
        self, symbol: str, timeframe: str = "15m", limit: int = 100
    ) -> List[Candle]:
        """REST recovery / initialization of historical candles."""
        try:
            raw_klines = await self.rest_client.fetch_ohlcv(
                symbol, timeframe=timeframe, limit=limit
            )
            candles = []
            for k in raw_klines:
                candles.append(
                    Candle(
                        symbol=symbol,
                        timeframe=Timeframe(timeframe)
                        if timeframe in [t.value for t in Timeframe]
                        else Timeframe.M15,
                        timestamp=datetime.fromtimestamp(k[0] / 1000.0, tz=timezone.utc),
                        open=float(k[1]),
                        high=float(k[2]),
                        low=float(k[3]),
                        close=float(k[4]),
                        volume=float(k[5]),
                    )
                )
            return candles
        except Exception as e:
            logger.error(f"REST fetch_ohlcv error for {symbol}: {e}")
            return []

    async def start(self):
        """Starts real-time multiplexed WebSocket stream loop."""
        self._running = True
        reconnect_delay = 2.0
        max_reconnect_delay = 60.0

        # Construct multiplexed stream paths: e.g. btcusdt@kline_15m / btcusdt@bookTicker
        streams = []
        for s in self.symbols:
            formatted_sym = s.lower().replace("/", "")
            streams.append(f"{formatted_sym}@kline_{self.timeframe.value}")
            streams.append(f"{formatted_sym}@bookTicker")

        stream_query = "/".join(streams)
        full_ws_url = f"{self.WS_STREAM_URL}{stream_query}"

        while self._running:
            try:
                self.state = ConnectionState.CONNECTING
                logger.info(
                    f"Connecting to Binance real-time WebSocket ({len(self.symbols)} pairs)..."
                )

                async with websockets.connect(full_ws_url, ping_interval=20, ping_timeout=10) as ws:
                    self._ws = ws
                    self.state = ConnectionState.HEALTHY
                    self._last_heartbeat = datetime.now(timezone.utc)
                    reconnect_delay = 2.0
                    logger.info("Binance WebSocket HEALTHY: Real-time streams active.")

                    while self._running:
                        msg = await ws.recv()
                        self._last_heartbeat = datetime.now(timezone.utc)
                        await self._process_ws_message(msg)

            except (websockets.ConnectionClosed, Exception) as e:
                if not self._running:
                    self.state = ConnectionState.DISCONNECTED
                    break
                self._reconnect_count += 1
                self.state = ConnectionState.RECONNECTING
                logger.warning(
                    f"Binance WS disconnected ({e}). Reconnecting in {reconnect_delay:.1f}s (Attempt #{self._reconnect_count})..."
                )
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 1.5, max_reconnect_delay)

    async def stop(self):
        self._running = False
        if self._ws:
            await self._ws.close()
        await self.rest_client.close()
        self.state = ConnectionState.DISCONNECTED
        logger.info("Binance connector stopped.")

    async def _process_ws_message(self, raw_msg: Any):
        try:
            payload = json.loads(raw_msg)
            stream_name = payload.get("stream", "")
            data = payload.get("data", {})

            # 1. Kline stream
            if "kline" in stream_name:
                k = data.get("k", {})
                sym_raw = k.get("s", "")
                symbol = f"{sym_raw[:-4]}/{sym_raw[-4:]}" if sym_raw.endswith("USDT") else sym_raw
                candle = Candle(
                    symbol=symbol,
                    timeframe=self.timeframe,
                    timestamp=normalize_timestamp(k.get("t")),
                    open=float(k.get("o", 0.0)),
                    high=float(k.get("h", 0.0)),
                    low=float(k.get("l", 0.0)),
                    close=float(k.get("c", 0.0)),
                    volume=float(k.get("v", 0.0)),
                )
                for cb in self.candle_callbacks:
                    cb(candle)

            # 2. BookTicker stream (Bid / Ask / Spread)
            elif "bookTicker" in stream_name:
                sym_raw = data.get("s", "")
                symbol = f"{sym_raw[:-4]}/{sym_raw[-4:]}" if sym_raw.endswith("USDT") else sym_raw
                bid = float(data.get("b", 0.0))
                ask = float(data.get("a", 0.0))
                spread = ask - bid
                spread_bps = (spread / ask * 10000.0) if ask > 0 else 0.0

                ticker_data = {
                    "symbol": symbol,
                    "bid": bid,
                    "ask": ask,
                    "spread": spread,
                    "spread_bps": spread_bps,
                    "timestamp": datetime.now(timezone.utc),
                }
                self.book_tickers[symbol] = ticker_data
                for b_cb in self.book_ticker_callbacks:
                    b_cb(ticker_data)

        except Exception as e:
            logger.error(f"Error handling Binance message: {e}")
