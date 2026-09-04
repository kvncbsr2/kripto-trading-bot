import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from apps.api.app.api.router import router as api_router
from database.init_db import init_models_async
from shared.config import get_settings
from shared.logging import get_logger

logger = get_logger("api-main", service="api")
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialize database on startup
    logger.info("Initializing database schemas...")
    await init_models_async()
    logger.info("KRIPTO AGENT API started successfully.")

    # Start Autonomous Paper Trader background loop
    try:
        from apps.api.app.api.router import command_bus
        from services.autonomous_runner import autonomous_trader
        autonomous_trader.bind_command_bus(command_bus)
        autonomous_trader.start()
        logger.info("Autonomous Paper Trader loop initialized and running.")
    except Exception as e:
        logger.error(f"Failed to start autonomous trader: {e}")

    yield

    try:
        from services.autonomous_runner import autonomous_trader
        autonomous_trader.stop()
    except Exception:
        pass
    logger.info("Shutting down KRIPTO AGENT API...")


app = FastAPI(
    title="KRIPTO AGENT API",
    description="Autonomous Algorithmic Crypto Paper-Trading Platform (V2 7-Day $5,000 Validation System)",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount all endpoints
app.include_router(api_router)


@app.get("/dashboard", response_class=HTMLResponse)
async def serve_dashboard():
    dashboard_path = os.path.join(os.path.dirname(__file__), "static", "dashboard.html")
    if os.path.exists(dashboard_path):
        with open(dashboard_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>KRIPTO AGENT Dashboard</h1><p>Static dashboard not found.</p>"


@app.get("/")
async def root():
    return {
        "name": "KRIPTO AGENT",
        "version": "2.0.0",
        "mode": "PAPER TRADING ONLY",
        "dashboard_url": "/dashboard",
        "status": "active",
        "docs_url": "/docs",
    }
