from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Response
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.api.state import RUNTIME_STATE
from database.session import get_async_db
from services.monitoring.metrics import get_prometheus_metrics
from shared.config import get_settings

router = APIRouter(tags=["health"])
settings = get_settings()


@router.get("/metrics")
async def get_metrics():
    """Prometheus metrics endpoint."""
    return Response(content=get_prometheus_metrics(), media_type="text/plain; version=0.0.4")


@router.get("/health")
async def health():
    return {
        "status": "ok",
        "system_state": RUNTIME_STATE.get("system_state", "READY"),
        "live_trading": settings.LIVE_TRADING,
        "paper_trading": settings.PAPER_TRADING,
        "experiment": settings.EXPERIMENT_NAME,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/ready")
@router.get("/health/ready")
async def health_ready(db: AsyncSession = Depends(get_async_db)):
    try:
        await db.execute(text("SELECT 1"))
        db_status = "healthy"
    except Exception as e:
        db_status = f"unhealthy: {e}"

    is_ready = "healthy" in db_status and RUNTIME_STATE.get("system_state") != "ERROR"
    return {
        "status": "ready" if is_ready else "degraded",
        "system_state": RUNTIME_STATE.get("system_state"),
        "database": db_status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/live")
async def health_live():
    return {
        "status": "alive",
        "system_state": RUNTIME_STATE.get("system_state", "READY"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
