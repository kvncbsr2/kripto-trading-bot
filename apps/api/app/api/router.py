"""
Authoritative API Router Aggregator for KRIPTO AGENT V6.1 (Requirement 32).
Decomposes the monolithic API into 13 dedicated routers while preserving unified routing.
"""
from fastapi import APIRouter

from apps.api.app.api.routers.agents_hub import router as agents_hub_router
from apps.api.app.api.routers.backtests import router as backtests_router
from apps.api.app.api.routers.binance import router as binance_router
from apps.api.app.api.routers.discovery import router as discovery_router
from apps.api.app.api.routers.experiments import router as experiments_router
from apps.api.app.api.routers.health import router as health_router
from apps.api.app.api.routers.market import router as market_router
from apps.api.app.api.routers.orders import router as orders_router
from apps.api.app.api.routers.agents_hub import router as agents_hub_router
from apps.api.app.api.routers.intelligence import router as intelligence_router
from apps.api.app.api.routers.news import router as news_router
from apps.api.app.api.routers.positions import router as positions_router
from apps.api.app.api.routers.risk import router as risk_router
from apps.api.app.api.routers.scanner import router as scanner_router
from apps.api.app.api.routers.signals import router as signals_router
from apps.api.app.api.routers.strategies import router as strategies_router
from apps.api.app.api.routers.system import router as system_router
from apps.api.app.api.state import (
    EXPERIMENT_RUNS,
    LATEST_REPLAY_REPORT,
    RUNTIME_STATE,
    command_bus,
    market_data_service,
    paper_broker,
    risk_engine,
    validation_engine,
)

router = APIRouter()

# Mount all 15 modular routers
router.include_router(health_router)
router.include_router(system_router)
router.include_router(market_router)
router.include_router(scanner_router)
router.include_router(signals_router)
router.include_router(positions_router)
router.include_router(orders_router)
router.include_router(risk_router)
router.include_router(strategies_router)
router.include_router(backtests_router)
router.include_router(discovery_router)
router.include_router(experiments_router)
router.include_router(binance_router)
router.include_router(agents_hub_router)
router.include_router(news_router)
router.include_router(intelligence_router)

__all__ = [
    "router",
    "command_bus",
    "RUNTIME_STATE",
    "risk_engine",
    "market_data_service",
    "paper_broker",
    "validation_engine",
    "LATEST_REPLAY_REPORT",
    "EXPERIMENT_RUNS",
]
