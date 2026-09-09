"""
Intelligence & Whale Radar API Router.
Exposes endpoints for real-time whale trade alerts, net flow, and institutional volume metrics.
"""

from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Query

from services.intelligence.whale_tracker import whale_tracker

router = APIRouter(tags=["intelligence"])


@router.get("/whales/recent")
@router.get("/api/v1/intelligence/whales/recent")
async def get_recent_whales(
    limit: int = Query(default=50, ge=1, le=150),
    min_usd: Optional[float] = Query(default=None, ge=0.0)
) -> List[Dict[str, Any]]:
    """Return the most recent whale trades detected on Binance Spot."""
    trades = await whale_tracker.get_recent_trades(limit=limit)
    if min_usd is not None and min_usd > 0:
        trades = [t for t in trades if t["total_usd"] >= min_usd]
    return trades


@router.get("/whales/flow")
@router.get("/api/v1/intelligence/whales/flow")
async def get_whale_flow() -> Dict[str, Any]:
    """Return aggregate net whale flow, buy ratio, sentiment, and per-symbol metrics."""
    return await whale_tracker.get_flow_summary()


@router.get("/whales/status")
@router.get("/api/v1/intelligence/whales/status")
async def get_whale_tracker_status() -> Dict[str, Any]:
    """Return status and configuration of the whale tracker worker."""
    summary = await whale_tracker.get_flow_summary()
    return {
        "is_running": whale_tracker.is_running(),
        "tracked_symbols_count": len(whale_tracker.symbols),
        "min_usd_threshold": whale_tracker.min_usd,
        "buffer_size": summary["total_trades_tracked"]
    }
