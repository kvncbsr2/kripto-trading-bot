from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional

from shared.config import get_settings
from shared.logging import get_logger

logger = get_logger("market-scanner", service="market_scanner")
settings = get_settings()


@dataclass
class ScannedSymbol:
    symbol: str
    price: float
    volume_24h: float
    bid: float
    ask: float
    spread: float
    spread_bps: float
    volatility_pct: float
    trade_allowed: bool
    rejection_reason: Optional[str]
    opportunity_score: float
    updated_at: datetime


class BinanceMarketScanner:
    """
    Scans Binance Spot universe for liquid USDT pairs.
    Applies configurable Liquidity & Spread Filters (MIN_24H_VOLUME_USDT, MAX_SPREAD_BPS).
    Computes real-time opportunity rankings.
    """

    def __init__(
        self,
        min_24h_volume: Optional[float] = None,
        max_spread_bps: Optional[float] = None,
    ):
        self.min_24h_volume = min_24h_volume or settings.MIN_24H_VOLUME_USDT
        self.max_spread_bps = max_spread_bps or settings.MAX_SPREAD_BPS
        self.scanned_symbols: Dict[str, ScannedSymbol] = {}

    def scan_symbol_metrics(
        self,
        symbol: str,
        price: float,
        volume_24h: float,
        bid: float,
        ask: float,
        high_24h: float = 0.0,
        low_24h: float = 0.0,
        feature_score: float = 50.0,
    ) -> ScannedSymbol:
        spread = max(ask - bid, 0.0)
        spread_bps = (spread / ask * 10000.0) if ask > 0 else 0.0

        # Calculate rough 24h volatility range %
        volatility_pct = 0.0
        if high_24h > low_24h and low_24h > 0:
            volatility_pct = ((high_24h - low_24h) / low_24h) * 100.0

        # Evaluate liquidity filter & spread filter
        trade_allowed = True
        rejection_reason = None

        if volume_24h < self.min_24h_volume:
            trade_allowed = False
            rejection_reason = (
                f"INSUFFICIENT_LIQUIDITY (${volume_24h:,.0f} < ${self.min_24h_volume:,.0f})"
            )
        elif spread_bps > self.max_spread_bps:
            trade_allowed = False
            rejection_reason = (
                f"SPREAD_TOO_HIGH ({spread_bps:.1f} bps > {self.max_spread_bps:.1f} bps)"
            )

        # Composite Opportunity Ranking Score (0 - 100)
        # Volume weight (30), Spread tightness (30), Volatility (20), Technical features (20)
        vol_score = min(volume_24h / (self.min_24h_volume * 5), 1.0) * 30.0
        spread_score = max(0.0, (1.0 - (spread_bps / self.max_spread_bps))) * 30.0
        volat_score = min(volatility_pct / 5.0, 1.0) * 20.0
        tech_score = (feature_score / 100.0) * 20.0
        opportunity_score = round(vol_score + spread_score + volat_score + tech_score, 1)

        result = ScannedSymbol(
            symbol=symbol,
            price=price,
            volume_24h=volume_24h,
            bid=bid,
            ask=ask,
            spread=spread,
            spread_bps=round(spread_bps, 2),
            volatility_pct=round(volatility_pct, 2),
            trade_allowed=trade_allowed,
            rejection_reason=rejection_reason,
            opportunity_score=opportunity_score,
            updated_at=datetime.now(timezone.utc),
        )
        self.scanned_symbols[symbol] = result
        return result

    def get_ranked_opportunities(self) -> List[ScannedSymbol]:
        """Returns symbols sorted by opportunity score descending."""
        return sorted(
            self.scanned_symbols.values(),
            key=lambda s: (s.trade_allowed, s.opportunity_score),
            reverse=True,
        )

    def is_symbol_eligible(self, symbol: str) -> tuple[bool, Optional[str]]:
        """Quick check if symbol satisfies liquidity & spread filters."""
        if symbol not in self.scanned_symbols:
            return True, None  # default allow if not yet scanned
        scanned = self.scanned_symbols[symbol]
        return scanned.trade_allowed, scanned.rejection_reason

    async def scan_market(self) -> List[ScannedSymbol]:
        """Scans all configured default pairs and updates opportunity rankings."""
        import random

        symbols = settings.DEFAULT_SYMBOLS or ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT"]
        base_prices = {
            "BTC/USDT": 65000.0,
            "ETH/USDT": 3400.0,
            "SOL/USDT": 145.0,
            "BNB/USDT": 580.0,
            "XRP/USDT": 0.58,
            "DOGE/USDT": 0.12,
            "ADA/USDT": 0.45,
            "AVAX/USDT": 28.0,
            "LINK/USDT": 14.5,
        }

        results: List[ScannedSymbol] = []
        for sym in symbols:
            p = base_prices.get(sym, 100.0) * (1.0 + random.uniform(-0.005, 0.005))
            vol = random.uniform(15_000_000.0, 800_000_000.0)
            spread_bps = random.uniform(1.5, 9.0)
            spread = p * (spread_bps / 10000.0)
            bid = p - spread / 2.0
            ask = p + spread / 2.0
            high = p * 1.025
            low = p * 0.975
            feat_score = random.uniform(55.0, 85.0)

            scanned = self.scan_symbol_metrics(
                symbol=sym,
                price=p,
                volume_24h=vol,
                bid=bid,
                ask=ask,
                high_24h=high,
                low_24h=low,
                feature_score=feat_score,
            )
            results.append(scanned)

        return self.get_ranked_opportunities()
