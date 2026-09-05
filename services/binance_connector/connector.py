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
    CONNECTED = "CONNECTED"
    HEALTHY = "CONNECTED"  # Backward compatibility alias
    DEGRADED = "DEGRADED"
    RECONNECTING = "RECONNECTING"
    FAILED = "FAILED"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"


class BinanceConnector:
    """
    Dedicated Binance Connector supporting Real-Time Multiplexed WebSockets
    (kline, bookTicker, miniTicker) and REST recovery.
    Includes state machine (DISCONNECTED -> CONNECTING -> CONNECTED -> RECONNECTING -> STOPPED).
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
        self.connection_timestamp: Optional[datetime] = None
        self.last_message_timestamp: Optional[datetime] = None
        self.last_event_timestamp: Optional[datetime] = None
        self.subscriptions: List[str] = []

        # Callbacks
        self.candle_callbacks: List[Callable[[Candle], None]] = []
        self.book_ticker_callbacks: List[Callable[[Dict[str, Any]], None]] = []

        # Local cache for book tickers: symbol -> {bid, ask, spread, spread_bps, timestamp}
        self.book_tickers: Dict[str, Dict[str, Any]] = {}

        # REST client (read-only)
        self.rest_client = ccxt.binance(
            {
                "enableRateLimit": True,
                "timeout": 4000,
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
            "connection_timestamp": self.connection_timestamp.isoformat() if self.connection_timestamp else None,
            "last_message_timestamp": self.last_message_timestamp.isoformat() if self.last_message_timestamp else None,
            "last_event_timestamp": self.last_event_timestamp.isoformat() if self.last_event_timestamp else None,
            "subscriptions": self.subscriptions,
            "live_trading_locked": True,
        }

    async def get_historical_candles(
        self,
        symbol: str,
        timeframe: str = "15m",
        limit: int = 100,
        since: Optional[int] = None,
        end_time: Optional[int] = None,
    ) -> List[Candle]:
        """REST recovery / initialization of historical candles respecting since and end_time."""
        try:
            kwargs = {}
            if since is not None:
                kwargs["since"] = since
            raw_klines = await asyncio.wait_for(
                self.rest_client.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit, **kwargs),
                timeout=4.0,
            )
            candles = []
            for k in raw_klines:
                k_ts_ms = k[0]
                if end_time is not None and k_ts_ms > end_time:
                    continue
                candles.append(
                    Candle(
                        symbol=symbol,
                        timeframe=Timeframe(timeframe)
                        if timeframe in [t.value for t in Timeframe]
                        else Timeframe.M15,
                        timestamp=datetime.fromtimestamp(k_ts_ms / 1000.0, tz=timezone.utc),
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
        """Starts real-time multiplexed WebSocket stream loop with jitter and exponential backoff."""
        if self._running:
            logger.info("Binance connector already running.")
            return

        import random

        self._running = True
        reconnect_delay = 2.0
        max_reconnect_delay = 60.0

        # Construct multiplexed stream paths: e.g. btcusdt@kline_15m / btcusdt@bookTicker
        streams = []
        for s in self.symbols:
            formatted_sym = s.lower().replace("/", "")
            streams.append(f"{formatted_sym}@kline_{self.timeframe.value}")
            streams.append(f"{formatted_sym}@bookTicker")

        self.subscriptions = streams
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
                    self.state = ConnectionState.CONNECTED
                    now_utc = datetime.now(timezone.utc)
                    self.connection_timestamp = now_utc
                    self._last_heartbeat = now_utc
                    self.last_message_timestamp = now_utc
                    reconnect_delay = 2.0
                    logger.info("Binance WebSocket CONNECTED: Real-time streams active.")

                    while self._running:
                        msg = await ws.recv()
                        now_msg = datetime.now(timezone.utc)
                        self._last_heartbeat = now_msg
                        self.last_message_timestamp = now_msg
                        await self._process_ws_message(msg)

            except (websockets.ConnectionClosed, Exception) as e:
                if not self._running:
                    self.state = ConnectionState.STOPPED
                    break
                self._reconnect_count += 1
                self.state = ConnectionState.RECONNECTING
                jitter = random.uniform(0.8, 1.2)
                wait_time = min(reconnect_delay * jitter, max_reconnect_delay)
                logger.warning(
                    f"Binance WS disconnected ({e}). Reconnecting in {wait_time:.1f}s (Attempt #{self._reconnect_count})..."
                )
                await asyncio.sleep(wait_time)
                reconnect_delay = min(reconnect_delay * 1.5, max_reconnect_delay)

    async def stop(self):
        self._running = False
        self.state = ConnectionState.STOPPING
        if self._ws:
            try:
                await self._ws.close()
            except Exception as e:
                logger.debug(f"Error closing WebSocket during shutdown: {e}")
        try:
            await self.rest_client.close()
        except Exception as e:
            logger.debug(f"Error closing REST client during shutdown: {e}")
        self.state = ConnectionState.STOPPED
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
