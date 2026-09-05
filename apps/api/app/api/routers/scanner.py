from datetime import datetime, timezone

from fastapi import APIRouter

from apps.api.app.api.state import market_data_service
from services.market_scanner.scanner import BinanceMarketScanner
from shared.config import get_settings

router = APIRouter(tags=["scanner"])
settings = get_settings()


@router.get("/scanner")
@router.get("/scanner/opportunities")
@router.get("/scanner/scan")
async def get_scanner_opportunities():
    """
    Scans monitored Binance symbols using REAL live market tickers.
    Zero mock/hardcoded candidate tables.
    """
    scanner = BinanceMarketScanner(
        min_24h_volume=settings.MIN_24H_VOLUME_USDT,
        max_spread_bps=settings.MAX_SPREAD_BPS,
    )

    tickers = await market_data_service.get_live_tickers(settings.DEFAULT_SYMBOLS)
    ticker_list = tickers if isinstance(tickers, list) else list(tickers.values()) if tickers else []

    for tick in ticker_list:
        sym = tick.get("symbol")
        if not sym:
            continue
        price = float(tick.get("price", 0.0))
        vol = float(tick.get("volume_24h", 0.0))
        bid = float(tick.get("bid", 0.0))
        ask = float(tick.get("ask", 0.0))
        hi = float(tick.get("high_24h", 0.0) or tick.get("high", price))
        lo = float(tick.get("low_24h", 0.0) or tick.get("low", price))

        if price <= 0.0 or bid <= 0.0 or ask <= 0.0 or ask < bid:
            continue

        spread_bps = float(tick.get("spread_bps", 0.0))
        if spread_bps <= 0.0 and ask > 0:
            spread_bps = ((ask - bid) / ask) * 10000.0
        spread_penalty = max(0.0, min(50.0, spread_bps * 2.0))
        f_score = max(10.0, min(95.0, 75.0 - spread_penalty))

        scanner.scan_symbol_metrics(sym, price, vol, bid, ask, hi, lo, f_score)

    ranked = scanner.get_ranked_opportunities()

    return {
        "monitored_universe_count": len(ticker_list),
        "min_volume_threshold": settings.MIN_24H_VOLUME_USDT,
        "max_spread_threshold_bps": settings.MAX_SPREAD_BPS,
        "ranked_symbols": [
            {
                "symbol": s.symbol,
                "price": s.price,
                "volume_24h": s.volume_24h,
                "spread_bps": s.spread_bps,
                "volatility_pct": s.volatility_pct,
                "trade_allowed": s.trade_allowed,
                "opportunity_score": s.opportunity_score,
                "rejection_reason": s.rejection_reason,
            }
            for s in ranked
        ],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/scanner/signals")
async def get_scanner_signals():
    """Returns detected opportunities that pass trading filters."""
    res = await get_scanner_opportunities()
    allowed = [s for s in res.get("ranked_symbols", []) if s.get("trade_allowed")]
    return {
        "active_signals_count": len(allowed),
        "signals": allowed,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
