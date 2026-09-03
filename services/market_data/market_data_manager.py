from typing import Dict, List, Optional

from database.repositories.candle_repo import CandleRepository
from database.session import AsyncSessionLocal
from services.market_data.collectors.rest_collector import RestMarketDataCollector
from shared.enums import Timeframe
from shared.logging import get_logger
from shared.schemas import Candle

logger = get_logger("market-data-manager", service="market_data")


class MarketDataManager:
    def __init__(self, symbols: Optional[List[str]] = None):
        self.symbols = symbols or ["BTC/USDT", "ETH/USDT"]
        self.rest_collector = RestMarketDataCollector()
        self._candle_cache: Dict[str, Dict[str, List[Candle]]] = {s: {} for s in self.symbols}

    async def initialize_history(self, symbol: str, timeframe: Timeframe, limit: int = 250):
        """Pre-populates candle cache with historical data via REST."""
        try:
            logger.info(f"Loading {limit} historical candles for {symbol} ({timeframe.value})...")
            candles = await self.rest_collector.fetch_ohlcv(
                symbol, timeframe=timeframe, limit=limit
            )
            if symbol not in self._candle_cache:
                self._candle_cache[symbol] = {}
            self._candle_cache[symbol][timeframe.value] = candles

            # Persist to database asynchronously
            async with AsyncSessionLocal() as session:
                repo = CandleRepository(session)
                for c in candles:
                    await repo.save_candle(c)
                await session.commit()
            logger.info(f"Loaded and persisted {len(candles)} candles for {symbol}.")
        except Exception as e:
            logger.error(f"Failed to load history for {symbol}: {e}")

    def on_new_candle(self, candle: Candle):
        """In-memory cache update for new incoming candle."""
        sym = candle.symbol
        tf = candle.timeframe.value
        if sym not in self._candle_cache:
            self._candle_cache[sym] = {}
        if tf not in self._candle_cache[sym]:
            self._candle_cache[sym][tf] = []

        candles = self._candle_cache[sym][tf]
        if candles and candles[-1].timestamp == candle.timestamp:
            candles[-1] = candle  # Update current forming candle
        else:
            candles.append(candle)
            if len(candles) > 1000:
                self._candle_cache[sym][tf] = candles[-1000:]

    def get_cached_candles(self, symbol: str, timeframe: Timeframe) -> List[Candle]:
        return self._candle_cache.get(symbol, {}).get(timeframe.value, [])

    async def close(self):
        await self.rest_collector.close()
