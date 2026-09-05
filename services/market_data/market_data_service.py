import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from services.binance_connector.connector import BinanceConnector
from services.market_data.data_quality import DataQualityEngine
from shared.config import get_settings
from shared.logging import get_logger
from shared.schemas import Candle

logger = get_logger("market-data-service", service="market_data")
settings = get_settings()


class MarketDataService:
    """
    Authoritative Market Data Service for KRIPTO AGENT V6.1.
    Coordinates:
    Binance Spot REST + Binance Spot WebSocket -> DataQualityEngine -> Redis/DB -> Strategy Engine.

    Guarantees:
    - Zero mock/random/synthetic price or volume injection.
    - Stale ticker prevention (strict freshness validation).
    """

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        connector: Optional[BinanceConnector] = None,
        max_ticker_age_seconds: float = 15.0,
    ):
        self.symbols = symbols or settings.DEFAULT_SYMBOLS
        self.connector = connector or BinanceConnector(symbols=self.symbols)
        self.quality_engine = DataQualityEngine()
        self.max_ticker_age_seconds = max_ticker_age_seconds
        self._candle_cache: Dict[str, Dict[str, List[Candle]]] = {s: {} for s in self.symbols}
        self._orderbook_cache: Dict[str, Dict[str, Any]] = {}
        self._ticker_cache: Dict[str, Dict[str, Any]] = {}
        self._is_started = False
        self._ws_task: Optional[asyncio.Task] = None

        # Register callbacks with connector
        self.connector.add_candle_callback(self._on_candle_received)
        self.connector.add_book_ticker_callback(self._on_book_ticker_received)

    def start(self):
        """Starts background WebSocket streaming and heartbeat."""
        if not self._is_started:
            self._is_started = True
            try:
                loop = asyncio.get_running_loop()
                self._ws_task = loop.create_task(self.connector.start())
            except RuntimeError:
                self._ws_task = None
            logger.info("MarketDataService started with BinanceConnector.")

    async def start_async(self):
        """Explicit async startup awaiting or creating background task."""
        if not self._is_started:
            self._is_started = True
            self._ws_task = asyncio.create_task(self.connector.start())
            logger.info("MarketDataService started asynchronously with BinanceConnector.")

    async def stop(self):
        if self._is_started:
            if self._ws_task and not self._ws_task.done():
                self._ws_task.cancel()
            await self.connector.stop()
            self._is_started = False
            logger.info("MarketDataService stopped.")

    # -------------------------------------------------------------------------
    # Callbacks & Data Quality Pipeline
    # -------------------------------------------------------------------------
    def _on_candle_received(self, candle: Candle):
        # Validate data quality (monotonicity, spread, anomalous return)
        cached = self._candle_cache.get(candle.symbol, {}).get(candle.timeframe.value, [])
        prev_candle = cached[-1] if cached else None
        res = self.quality_engine.validate_candle(candle, prev_candle=prev_candle)

        if not res.valid:
            logger.warning(
                f"Data quality rejection for {candle.symbol} {candle.timeframe.value}: {res.reason}"
            )
            return

        sym = candle.symbol
        tf = candle.timeframe.value
        if sym not in self._candle_cache:
            self._candle_cache[sym] = {}
        if tf not in self._candle_cache[sym]:
            self._candle_cache[sym][tf] = []

        candles = self._candle_cache[sym][tf]
        if candles and candles[-1].timestamp == candle.timestamp:
            candles[-1] = candle
        else:
            candles.append(candle)
            if len(candles) > 1500:
                self._candle_cache[sym][tf] = candles[-1500:]

    def _on_book_ticker_received(self, data: Dict[str, Any]):
        sym = data.get("symbol")
        if sym:
            now_ts = time.time()
            self._ticker_cache[sym] = {
                "symbol": sym,
                "bid": float(data.get("bid", 0.0)),
                "ask": float(data.get("ask", 0.0)),
                "price": round((float(data.get("bid", 0.0)) + float(data.get("ask", 0.0))) / 2.0, 4),
                "spread": float(data.get("spread", 0.0)),
                "spread_bps": float(data.get("spread_bps", 0.0)),
                "timestamp": data.get("timestamp", datetime.now(timezone.utc).isoformat()),
                "received_at": now_ts,
                "source": "BINANCE_REALTIME_WS",
            }

    # -------------------------------------------------------------------------
    # Public Authoritative Query API (Zero Fake Data)
    # -------------------------------------------------------------------------
    async def get_live_ticker(self, symbol: str) -> Optional[Dict[str, Any]]:
        """
        Returns real-time Binance ticker with strict freshness guarantee.
        If WebSocket cache is fresh (<= max_ticker_age_seconds), returns cached ticker.
        If stale or missing, queries Binance REST directly and updates cache.
        Returns None if unreachable (Zero Fake Data Policy).
        """
        now_ts = time.time()

        # 1. Check WebSocket local cache with strict age verification
        if symbol in self._ticker_cache:
            cached = self._ticker_cache[symbol]
            age = now_ts - cached.get("received_at", 0.0)
            if age <= self.max_ticker_age_seconds:
                return cached
            logger.warning(
                f"WebSocket ticker cache for {symbol} is stale ({age:.1f}s > {self.max_ticker_age_seconds}s). Attempting REST refresh."
            )

        # 2. Query Binance REST via CCXT
        try:
            raw = await self.connector.rest_client.fetch_ticker(symbol)
            if raw and raw.get("bid") and raw.get("ask"):
                bid = float(raw["bid"])
                ask = float(raw["ask"])
                spread = max(0.0, ask - bid)
                spread_bps = (spread / ask * 10000.0) if ask > 0 else 0.0
                price = float(raw.get("last") or ((bid + ask) / 2.0))
                volume_24h = float(raw.get("quoteVolume") or 0.0)

                ticker_data = {
                    "symbol": symbol,
                    "price": price,
                    "bid": bid,
                    "ask": ask,
                    "spread": spread,
                    "spread_bps": round(spread_bps, 2),
                    "volume_24h": volume_24h,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "received_at": now_ts,
                    "source": "BINANCE_REST",
                }
                self._ticker_cache[symbol] = ticker_data
                return ticker_data
        except Exception as e:
            logger.error(f"Failed to fetch live Binance ticker for {symbol}: {e}")

        return None

    async def get_live_tickers(self, symbols: Optional[List[str]] = None) -> List[Dict[str, Any]]:
        """Returns live tickers for requested symbols. Omits any symbol where real data is unavailable."""
        target_symbols = symbols or self.symbols
        results = []
        for s in target_symbols:
            t = await self.get_live_ticker(s)
            if t is not None:
                results.append(t)
        return results

    async def get_orderbook(self, symbol: str, limit: int = 20) -> Optional[Dict[str, Any]]:
        """
        Fetches live orderbook snapshot from Binance.
        Returns None if unavailable (Zero Fake Data Policy).
        """
        try:
            ob = await self.connector.rest_client.fetch_order_book(symbol, limit=limit)
            if ob and "bids" in ob and "asks" in ob and len(ob["bids"]) > 0 and len(ob["asks"]) > 0:
                best_bid = float(ob["bids"][0][0])
                best_ask = float(ob["asks"][0][0])
                spread = best_ask - best_bid
                spread_bps = (spread / best_ask * 10000.0) if best_ask > 0 else 0.0

                return {
                    "symbol": symbol,
                    "bid": best_bid,
                    "ask": best_ask,
                    "spread": round(spread, 6),
                    "spread_bps": round(spread_bps, 2),
                    "bids": [[float(p), float(q)] for p, q in ob["bids"][:limit]],
                    "asks": [[float(p), float(q)] for p, q in ob["asks"][:limit]],
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "source": "BINANCE_REST_SNAPSHOT",
                }
        except Exception as e:
            logger.error(f"Failed to fetch real orderbook for {symbol}: {e}")

        return None

    def is_ticker_fresh(self, symbol: str, max_age_seconds: float = 3.0) -> bool:
        """Returns True if ticker exists in cache and was updated within max_age_seconds."""
        t = self._ticker_cache.get(symbol)
        if not t or "timestamp" not in t:
            return False
        ts = t["timestamp"]
        if isinstance(ts, str):
            try:
                dt = datetime.fromisoformat(ts)
            except Exception:
                return False
        elif isinstance(ts, datetime):
            dt = ts
        else:
            return False
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - dt).total_seconds()
        return age <= max_age_seconds

    def is_candle_fresh(self, symbol: str, timeframe: str = "15m", max_age_seconds: float = 180.0) -> bool:
        """Returns True if latest candle for symbol/timeframe is within max_age_seconds."""
        candles = self._candle_cache.get(symbol, {}).get(timeframe, [])
        if not candles:
            return False
        latest_ts = candles[-1].timestamp
        if latest_ts.tzinfo is None:
            latest_ts = latest_ts.replace(tzinfo=timezone.utc)
        age = (datetime.now(timezone.utc) - latest_ts).total_seconds()
        return age <= max_age_seconds

    async def get_historical_candles(
        self,
        symbol: str,
        timeframe: str = "15m",
        limit: int = 100,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> List[Candle]:
        """
        Fetches historical candles from Binance REST.
        Passes since and end_time timestamps to ccxt.
        Zero synthetic fallback.
        """
        try:
            since = int(start.timestamp() * 1000) if start else None
            end_time = int(end.timestamp() * 1000) if end else None
            candles = await self.connector.get_historical_candles(
                symbol=symbol,
                timeframe=timeframe,
                limit=limit,
                since=since,
                end_time=end_time,
            )
            return candles
        except Exception as e:
            logger.error(f"Failed to fetch historical candles for {symbol} ({timeframe}): {e}")
            return []

    async def get_historical_klines(
        self,
        symbol: str,
        timeframe: str = "15m",
        limit: int = 100,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> List[Candle]:
        return await self.get_historical_candles(symbol, timeframe, limit, start, end)

    async def close(self):
        """Cleanly terminates connector and closes all network sessions."""
        await self.stop()


# Global authoritative singleton instance
market_data_service = MarketDataService()
