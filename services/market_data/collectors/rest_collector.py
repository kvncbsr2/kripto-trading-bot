import asyncio
from typing import List, Optional

import ccxt.async_support as ccxt_async

from services.market_data.normalizer.normalizer import DataNormalizer
from shared.enums import ExchangeName, Timeframe
from shared.logging import get_logger
from shared.schemas import Candle

logger = get_logger("rest-collector", service="market_data")


class RestMarketDataCollector:
    def __init__(self, exchange_id: str = "binance"):
        self.exchange_id = exchange_id
        exchange_class = getattr(ccxt_async, exchange_id, ccxt_async.binance)
        self.client = exchange_class(
            {
                "enableRateLimit": True,
                "timeout": 20000,
            }
        )

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: Timeframe = Timeframe.M1,
        since: Optional[int] = None,
        limit: int = 100,
        max_retries: int = 3,
    ) -> List[Candle]:
        tf_str = timeframe.value
        for attempt in range(1, max_retries + 1):
            try:
                raw_candles = await self.client.fetch_ohlcv(
                    symbol=symbol,
                    timeframe=tf_str,
                    since=since,
                    limit=limit,
                )
                candles = [
                    DataNormalizer.normalize_ohlcv(
                        raw_candle=rc,
                        symbol=symbol,
                        timeframe=timeframe,
                        exchange=ExchangeName.BINANCE,
                    )
                    for rc in raw_candles
                ]
                return candles
            except Exception as e:
                logger.warning(
                    f"Attempt {attempt}/{max_retries} failed to fetch OHLCV for {symbol} ({tf_str}): {e}"
                )
                if attempt == max_retries:
                    logger.error(f"Exhausted retries fetching OHLCV for {symbol}")
                    raise
                await asyncio.sleep(2**attempt)
        return []

    async def fetch_ticker(self, symbol: str):
        try:
            raw_ticker = await self.client.fetch_ticker(symbol)
            return DataNormalizer.normalize_ticker(raw_ticker, symbol)
        except Exception as e:
            logger.error(f"Error fetching ticker for {symbol}: {e}")
            raise

    async def close(self):
        await self.client.close()
