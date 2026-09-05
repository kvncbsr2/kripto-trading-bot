"""
Real Crypto Sentiment Service.
Ingests real-time Fear & Greed Index from Alternative.me (public, free crypto sentiment API).
Maintains an in-memory cache to respect rate limits and reduce latency.
"""

import time
from typing import Any, Dict, Optional

import httpx

from shared.logging import get_logger

logger = get_logger("crypto-sentiment", service="market_data")


class CryptoSentimentService:
    def __init__(self, cache_ttl_seconds: int = 600):
        self.url = "https://api.alternative.me/fng/?limit=1"
        self.cache_ttl = cache_ttl_seconds
        self._cached_data: Optional[Dict[str, Any]] = None
        self._last_fetch_ts: float = 0.0

    async def get_fear_and_greed_index(self) -> Dict[str, Any]:
        """
        Returns Fear & Greed index score (0-100) and classification.
        Cached for cache_ttl seconds.
        """
        now = time.time()
        if self._cached_data and (now - self._last_fetch_ts) < self.cache_ttl:
            return self._cached_data

        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.get(self.url)
                if resp.status_code == 200:
                    payload = resp.json()
                    data_items = payload.get("data", [])
                    if data_items:
                        item = data_items[0]
                        value = int(item.get("value", 50))
                        classification = item.get("value_classification", "Neutral")

                        # Normalized score between -1.0 (Extreme Fear) and +1.0 (Extreme Greed)
                        normalized_score = round((value - 50) / 50.0, 3)

                        result = {
                            "value": value,
                            "classification": classification,
                            "normalized_score": normalized_score,
                            "timestamp": int(item.get("timestamp", now)),
                            "is_stale": False,
                            "source": "alternative.me",
                        }
                        self._cached_data = result
                        self._last_fetch_ts = now
                        logger.info(f"Updated Fear & Greed Index: {value} ({classification})")
                        return result
        except Exception as e:
            logger.warning(f"Could not fetch Fear & Greed Index from alternative.me: {e}")

        # Fallback to cached or neutral if network fails
        if self._cached_data:
            stale_copy = dict(self._cached_data)
            stale_copy["is_stale"] = True
            return stale_copy

        return {
            "value": 50,
            "classification": "Neutral",
            "normalized_score": 0.0,
            "timestamp": int(now),
            "is_stale": True,
            "source": "fallback",
        }


# Global Singleton
sentiment_service = CryptoSentimentService()
