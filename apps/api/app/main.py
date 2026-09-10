import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from apps.api.app.api.router import router as api_router
from apps.api.app.api.state import RUNTIME_STATE, command_bus, market_data_service, risk_engine
from database.init_db import init_models_async
from services.autonomous_runner import autonomous_trader
from shared.config import get_settings
from shared.logging import get_logger

logger = get_logger("api-main", service="api")
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. State transition: STARTING
    RUNTIME_STATE["system_state"] = "STARTING"
    logger.info("Initializing database schemas [STARTING]...")
    try:
        import asyncio
        await asyncio.wait_for(init_models_async(), timeout=3.0)
    except Exception as e:
        logger.warning(f"Database initialization warning (offline fallback active): {e}")

    # 2. State transition: CONNECTING
    RUNTIME_STATE["system_state"] = "CONNECTING"
    logger.info("Connecting MarketDataService to Binance feed [CONNECTING]...")
    try:
        market_data_service.start()
        import asyncio
        asyncio.create_task(market_data_service.warm_up_cache())
    except Exception as e:
        logger.warning(f"MarketDataService background connector warning: {e}")

    # 3. State transition: READY
    RUNTIME_STATE["system_state"] = "READY"
    logger.info("System state transitioned to [READY].")

    # Wire Autonomous Trader
    autonomous_trader.bind_command_bus(command_bus)
    autonomous_trader.bind_market_data_service(market_data_service)
    autonomous_trader.risk_engine = risk_engine

    # Synchronize and restore persisted Risk Profile and Strategy (Architecture V2)
    try:
        from services.config_manager.risk_profiles import restore_runtime_system_state
        restore_runtime_system_state()
    except Exception as e:
        logger.warning(f"Failed to restore persisted profile state: {e}")

    # Auto-start Autonomous Trader loop (7/24 Continuous Cloud Execution)
    if getattr(settings, "AUTONOMOUS_AUTO_START", True):
        try:
            autonomous_trader.start()
            logger.info("Autonomous Paper Trader loop auto-started on boot.")
        except Exception as e:
            logger.warning(f"Autonomous Paper Trader auto-start warning: {e}")
    else:
        autonomous_trader.is_active = False
        logger.info("Autonomous Paper Trader loop initialized (OFF by default).")

    # Start Telegram background listener if enabled
    telegram_task = None
    try:
        from services.notification_service.telegram_service import telegram_service
        telegram_service.reload_config()
        if telegram_service.enabled:
            telegram_task = asyncio.create_task(telegram_service.start_polling())
            logger.info("Telegram command listener started in background.")
    except Exception as e:
        logger.warning(f"Telegram listener startup warning: {e}")

    # Start Whale Radar background tracker
    try:
        from services.intelligence.whale_tracker import whale_tracker
        whale_tracker.start()
        logger.info("Whale Radar background service started.")
    except Exception as e:
        logger.warning(f"Failed to start Whale Radar service: {e}")

    yield

    # Teardown
    logger.info("Shutting down KRIPTO AGENT API...")
    RUNTIME_STATE["system_state"] = "SHUTDOWN"
    try:
        if telegram_task and not telegram_task.done():
            from services.notification_service.telegram_service import telegram_service
            telegram_service.stop_polling()
            telegram_task.cancel()
        from services.intelligence.whale_tracker import whale_tracker
        await whale_tracker.stop()
        autonomous_trader.stop()
        await market_data_service.stop()
    except Exception as e:
        logger.warning(f"Shutdown cleanup warning: {e}")
    logger.info("KRIPTO AGENT API shutdown complete.")


app = FastAPI(
    title="KRIPTO AGENT API",
    description="Autonomous Algorithmic Crypto Paper-Trading Platform (V6.1 Causal Hardened)",
    version="6.1.0",
    lifespan=lifespan,
)

# CORS configuration strictly restricted (Requirement 32)
ALLOWED_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://localhost:8000",
    "http://127.0.0.1:8000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Mount all endpoints via modular aggregator
app.include_router(api_router)

static_dir = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/dashboard.html", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse)
@app.get("/", response_class=HTMLResponse)
async def serve_dashboard(request: Request, response: Response):
    dashboard_path = os.path.join(os.path.dirname(__file__), "static", "dashboard.html")
    if os.path.exists(dashboard_path):
        is_secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").lower() == "https"
        response.set_cookie(
            key="kripto_admin_token",
            value=settings.API_ADMIN_KEY,
            httponly=True,
            secure=is_secure,
            samesite="lax",
            path="/",
        )
        with open(dashboard_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>KRIPTO AGENT Dashboard</h1><p>Static dashboard not found. Use Next.js app on port 3000.</p>"
