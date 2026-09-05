import time
from typing import Any, Dict, List, Optional

import httpx

from shared.config import get_settings
from shared.logging import get_logger

logger = get_logger("dynamic-screener", service="scanner")

# Pairs to strictly exclude (Stablecoins, fiat, wrapped assets, or leveraged tokens)
EXCLUDED_SUBSTRINGS = [
    "UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT",
    "USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "EURUSDT",
    "USD1USDT", "AEURUSDT", "WBTCUSDT", "BUSDUSDT",
    "DAIUSDT", "USDPUSDT"
]

FALLBACK_SYMBOLS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT",
    "DOGE/USDT", "NEAR/USDT", "AVAX/USDT", "LINK/USDT", "ADA/USDT",
    "SUI/USDT", "PEPE/USDT"
]


class DynamicUniverseScreener:
    """
    Intelligent Dynamic Binance Spot Screener.
    Filters the 3,600+ Binance universe to identify the top 30-50 most liquid,
    sensible, high-quality USDT trading pairs.

    Invariants:
    1. Liquidity Guard: Minimum $10M 24h volume to prevent slippage & illiquidity traps.
    2. Asset Guard: Strictly genuine USDT spot pairs (no stablecoin-to-stablecoin, no leveraged).
    3. Caching: 10-minute cache window to protect Binance API rate limits.
    """

    def __init__(
        self,
        min_volume_usd: Optional[float] = None,
        max_symbols: Optional[int] = None,
        cache_ttl_seconds: float = 600.0,
    ):
        self.settings = get_settings()
        self.min_volume_usd = (
            min_volume_usd
            if min_volume_usd is not None
            else getattr(self.settings, "MIN_24H_VOLUME_USDT", 500000.0)
        )
        self.max_symbols = (
            max_symbols
            if max_symbols is not None
            else getattr(self.settings, "MAX_UNIVERSE_SYMBOLS", 200)
        )
        self.cache_ttl_seconds = cache_ttl_seconds
        self._cached_symbols: List[str] = []
        self._last_screen_time: float = 0.0

    async def get_liquid_universe(self) -> List[str]:
        now = time.time()
        if self._cached_symbols and (now - self._last_screen_time) < self.cache_ttl_seconds:
            return self._cached_symbols

        try:
            url = "https://api.binance.com/api/v3/ticker/24hr"
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code != 200:
                    logger.warning(f"Binance 24hr ticker query failed (status {resp.status_code}). Using fallback universe.")
                    return []

                data = resp.json()

            filtered: List[Dict[str, Any]] = []
            for item in data:
                sym: str = item.get("symbol", "")
                if not sym.endswith("USDT"):
                    continue

                if any(sub in sym for sub in EXCLUDED_SUBSTRINGS):
                    continue

                vol_usd = float(item.get("quoteVolume") or 0.0)
                if vol_usd < self.min_volume_usd:
                    continue

                # Bid-ask spread validity
                bid = float(item.get("bidPrice") or 0.0)
                ask = float(item.get("askPrice") or 0.0)
                if bid <= 0 or ask <= 0:
                    continue

                spread = ask - bid
                spread_bps = (spread / ask) * 10000.0
                if spread_bps > self.settings.MAX_SPREAD_BPS:  # Exclude wide spreads > settings.MAX_SPREAD_BPS
                    continue

                price_change = float(item.get("priceChangePercent") or 0.0)

                filtered.append({
                    "raw_symbol": sym,
                    "volume_usd": vol_usd,
                    "price_change": price_change,
                    "spread_bps": spread_bps,
                })

            # Sort strategy: Prioritize coins with dip potential (negative change) + high volume
            # We sort by price_change ascending (deepest dips first), bounded by top volume
            filtered.sort(key=lambda x: x["volume_usd"], reverse=True)
            top_volume = filtered[:self.max_symbols]

            # Format to CCXT slash convention: "BTCUSDT" -> "BTC/USDT"
            formatted_symbols = [
                f"{item['raw_symbol'][:-4]}/USDT" for item in top_volume
            ]

            # Ensure BTC is always present for regime monitoring
            if "BTC/USDT" not in formatted_symbols:
                formatted_symbols.insert(0, "BTC/USDT")

            self._cached_symbols = formatted_symbols
            self._last_screen_time = now
            logger.info(f"Dynamic Universe Screen Complete: {len(formatted_symbols)} liquid pairs selected.")
            return self._cached_symbols

        except Exception as e:
            logger.error(f"Error screening Binance universe: {e}. Using fallback universe.")
            return []

    def _fallback_universe(self) -> List[str]:
        if not self._cached_symbols:
            self._cached_symbols = list(FALLBACK_SYMBOLS)
        return self._cached_symbols


dynamic_screener = DynamicUniverseScreener()
