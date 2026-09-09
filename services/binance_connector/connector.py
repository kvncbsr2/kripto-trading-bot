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
    WS_STREAM_URLS = [
        "wss://data-stream.binance.vision/stream?streams=",
        "wss://stream.binance.com:9443/stream?streams=",
    ]

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
        self._processed_candle_keys: set = set()


        # REST client (read-only, strictly spot, uses data-api.binance.vision to avoid US cloud geo-blocking 451)
        self.rest_client = ccxt.binance(
            {
                "enableRateLimit": True,
                "timeout": 5000,
                "options": {
                    "defaultType": "spot",
                    "fetchMarkets": ["spot"],
                },
                "urls": {
                    "api": {
                        "public": "https://data-api.binance.vision/api/v3",
                        "fapiPublic": "https://data-api.binance.vision/api/v3",
                    }
                },
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
                timeout=25.0,
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

            # FIX (2026-09, critical, re-applied): Binance's klines endpoint includes
            # the CURRENTLY FORMING candle as the last element by default — its
            # high/low/close keep changing until the bar actually closes. Every
            # strategy in this codebase (especially R10's "Strict Causal
            # Anti-Lookahead Guarantee") assumes the last row of the dataframe is a
            # CLOSED bar. Left unfixed, a pivot's confirmation window could include
            # this still-mutating candle, letting a signal fire on a high/low that
            # hasn't finished forming — a genuine repainting risk. Drop the last
            # candle here if it hasn't closed yet, so every consumer downstream only
            # ever sees closed bars.
            tf_seconds = {
                "1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400,
            }.get(timeframe, 900)
            if candles:
                last = candles[-1]
                bar_close_time = last.timestamp.timestamp() + tf_seconds
                if bar_close_time > datetime.now(timezone.utc).timestamp():
                    candles.pop()

            return candles
        except asyncio.TimeoutError:
            logger.warning(f"REST fetch_ohlcv timed out (25s) for {symbol}")
            return []
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

        url_idx = 0
        while self._running:
            base_url = self.WS_STREAM_URLS[url_idx % len(self.WS_STREAM_URLS)]
            full_ws_url = f"{base_url}{stream_query}"
            try:
                self.state = ConnectionState.CONNECTING
                logger.info(
                    f"Connecting to Binance real-time WebSocket ({len(self.symbols)} pairs) via {base_url[:35]}..."
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
                url_idx += 1
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
                
                # Strict anti-lookahead / anti-repainting rule: Only process closed candles (x=True)
                is_closed = bool(k.get("x", False))
                if not is_closed:
                    return

                candle_ts = normalize_timestamp(k.get("t"))
                tf_val = self.timeframe.value if hasattr(self.timeframe, "value") else str(self.timeframe)
                candle_key = (symbol, tf_val, int(candle_ts.timestamp()))

                # Deduplication: Drop duplicate closed candles (e.g. from reconnects)
                if candle_key in self._processed_candle_keys:
                    logger.debug(f"Duplicate closed candle ignored: {candle_key}")
                    return

                self._processed_candle_keys.add(candle_key)
                if len(self._processed_candle_keys) > 5000:
                    self._processed_candle_keys = set(list(self._processed_candle_keys)[-2500:])

                candle = Candle(
                    symbol=symbol,
                    timeframe=self.timeframe,
                    timestamp=candle_ts,
                    open=float(k.get("o", 0.0)),
                    high=float(k.get("h", 0.0)),
                    low=float(k.get("l", 0.0)),
                    close=float(k.get("c", 0.0)),
                    volume=float(k.get("v", 0.0)),
                    is_closed=True,
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
