"""
Binance Derivatives & Institutional Positioning Data Service.
Fetches real public metrics from Binance Futures REST endpoints:
- Open Interest (OI)
- Global Long/Short Account Ratio
- Top Trader Long/Short Position Ratio
- Taker Buy/Sell Volume Ratio
"""

import time
from typing import Any, Dict

import httpx

from shared.logging import get_logger

logger = get_logger("binance-derivatives", service="market_data")


class BinanceDerivativesService:
    def __init__(self, cache_ttl_seconds: int = 120):
        self.base_url = "https://fapi.binance.com"
        self.cache_ttl = cache_ttl_seconds
        self._cache: Dict[str, Dict[str, Any]] = {}

    def _format_symbol(self, symbol: str) -> str:
        """Converts BTC/USDT to BTCUSDT."""
        return symbol.replace("/", "").upper()

    async def get_derivatives_metrics(self, symbol: str = "BTC/USDT") -> Dict[str, Any]:
        """
        Fetches consolidated futures metrics for a symbol with TTL caching.
        """
        raw_symbol = self._format_symbol(symbol)
        now = time.time()

        cached = self._cache.get(raw_symbol)
        if cached and (now - cached["_cached_at"]) < self.cache_ttl:
            return cached["data"]

        result = {
            "symbol": symbol,
            "raw_symbol": raw_symbol,
            "open_interest": None,
            "long_short_ratio": 1.0,
            "top_trader_long_short_ratio": 1.0,
            "taker_buy_sell_ratio": 1.0,
            "timestamp": int(now),
            "is_valid": False,
        }

        try:
            async with httpx.AsyncClient(timeout=6.0) as client:
                # 1. Open Interest
                try:
                    oi_res = await client.get(
                        f"{self.base_url}/fapi/v1/openInterest",
                        params={"symbol": raw_symbol},
                    )
                    if oi_res.status_code == 200:
                        oi_data = oi_res.json()
                        result["open_interest"] = float(oi_data.get("openInterest", 0.0))
                except Exception as e:
                    logger.debug(f"OI fetch error for {raw_symbol}: {e}")

                # 2. Global Long/Short Ratio
                try:
                    ls_res = await client.get(
                        f"{self.base_url}/futures/data/globalLongShortAccountRatio",
                        params={"symbol": raw_symbol, "period": "15m", "limit": 1},
                    )
                    if ls_res.status_code == 200:
                        ls_data = ls_res.json()
                        if ls_data and isinstance(ls_data, list):
                            result["long_short_ratio"] = float(ls_data[0].get("longShortRatio", 1.0))
                except Exception as e:
                    logger.debug(f"L/S ratio fetch error for {raw_symbol}: {e}")

                # 3. Top Trader Long/Short Position Ratio
                try:
                    top_res = await client.get(
                        f"{self.base_url}/futures/data/topLongShortPositionRatio",
                        params={"symbol": raw_symbol, "period": "15m", "limit": 1},
                    )
                    if top_res.status_code == 200:
                        top_data = top_res.json()
                        if top_data and isinstance(top_data, list):
                            result["top_trader_long_short_ratio"] = float(top_data[0].get("longShortRatio", 1.0))
                except Exception as e:
                    logger.debug(f"Top trader ratio fetch error for {raw_symbol}: {e}")

                # 4. Taker Buy/Sell Volume Ratio
                try:
                    taker_res = await client.get(
                        f"{self.base_url}/futures/data/takerlongshortRatio",
                        params={"symbol": raw_symbol, "period": "15m", "limit": 1},
                    )
                    if taker_res.status_code == 200:
                        taker_data = taker_res.json()
                        if taker_data and isinstance(taker_data, list):
                            result["taker_buy_sell_ratio"] = float(taker_data[0].get("buySellRatio", 1.0))
                except Exception as e:
                    logger.debug(f"Taker ratio fetch error for {raw_symbol}: {e}")

                result["is_valid"] = True
                self._cache[raw_symbol] = {"data": result, "_cached_at": now}
                return result

        except Exception as e:
            logger.warning(f"Derivatives service network error for {symbol}: {e}")
            if cached:
                return cached["data"]
            return result


# Global Singleton
derivatives_service = BinanceDerivativesService()
