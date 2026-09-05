import hashlib
import hmac
import json
import time
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from apps.api.app.middleware.auth import Role, verify_api_key_or_token
from shared.config import get_settings
from shared.logging import get_logger

router = APIRouter(tags=["binance"])
settings = get_settings()
logger = get_logger("binance-router", service="api")


class BinanceConnectRequest(BaseModel):
    api_key: str
    api_secret: str
    environment: str = "testnet"  # "testnet" or "production_market_data"


CONNECTED_BINANCE_ACCOUNT: Dict[str, Any] = {
    "connected": False,
    "environment": settings.BINANCE_ENV,
    "api_key_masked": "Tanımlanmadı",
    "balances": [],
    "last_checked": None,
}


@router.get("/binance/status")
async def get_binance_status():
    from services.market_data.market_data_service import market_data_service
    connector_status = market_data_service.connector.get_status()
    ws_state = connector_status.get("state", "DISCONNECTED")

    return {
        "exchange": "binance",
        "market": "spot",
        "environment": settings.BINANCE_ENVIRONMENT,
        "websocket_status": ws_state,
        "rest_status": "ONLINE",
        "symbols_monitored": settings.DEFAULT_SYMBOLS,
        "reconnect_count": connector_status.get("reconnect_count", 0),
        "last_heartbeat": connector_status.get("last_heartbeat"),
        "live_trading_locked": True,
        "guardrail": "LIVE TRADING IS LOCKED DURING VALIDATION",
        "is_spot_mode": True,
    }


@router.get("/binance/test-connection")
async def get_binance_test_connection():
    try:
        url = "https://api.binance.com/api/v3/ping"
        req = urllib.request.Request(url, headers={"User-Agent": "KriptoAgent/6.0"})
        t0 = time.perf_counter()
        with urllib.request.urlopen(req, timeout=5) as resp:
            t1 = time.perf_counter()
            latency_ms = round((t1 - t0) * 1000.0, 1)
            if resp.status == 200:
                return {
                    "status": "ONLINE",
                    "exchange": "binance",
                    "latency_ms": latency_ms,
                    "message": f"Binance Public REST API erişilebilir ({latency_ms}ms).",
                }
    except Exception as e:
        return {
            "status": "OFFLINE",
            "exchange": "binance",
            "error": str(e),
            "message": "Binance Public REST API erişilemedi.",
        }


@router.get("/api/v1/binance/account-status")
async def get_binance_account_status():
    """Returns real-time Binance connection and account balance state."""
    return CONNECTED_BINANCE_ACCOUNT


@router.post("/binance/connect")
@router.post("/api/v1/binance/connect")
async def connect_binance_account(
    payload: BinanceConnectRequest,
    _role: Role = Depends(verify_api_key_or_token),
):
    """
    Connects to user's Binance Demo/Testnet account.
    Verifies API signature and retrieves real account balances with Zero Fake Data.
    """
    key = payload.api_key.strip()
    secret = payload.api_secret.strip()

    if not key or not secret:
        raise HTTPException(status_code=400, detail="API Key ve Secret boş olamaz.")

    is_testnet = payload.environment == "testnet"
    base_url = (
        "https://testnet.binance.vision/api/v3"
        if is_testnet
        else "https://api.binance.com/api/v3"
    )

    try:
        ts = int(time.time() * 1000)
        query = f"timestamp={ts}"
        signature = hmac.new(
            secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        url = f"{base_url}/account?{query}&signature={signature}"
        req = urllib.request.Request(
            url,
            headers={
                "X-MBX-APIKEY": key,
                "User-Agent": "KriptoAgent/6.0",
            },
        )

        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read().decode())
            raw_balances = data.get("balances", [])
            real_balances = [
                {
                    "asset": b["asset"],
                    "free": float(b["free"]),
                    "locked": float(b["locked"]),
                }
                for b in raw_balances
                if float(b["free"]) > 0 or float(b["locked"]) > 0
            ]

            CONNECTED_BINANCE_ACCOUNT["connected"] = True
            CONNECTED_BINANCE_ACCOUNT["environment"] = payload.environment
            CONNECTED_BINANCE_ACCOUNT["api_key_masked"] = f"{key[:6]}...{key[-4:]}"
            CONNECTED_BINANCE_ACCOUNT["balances"] = real_balances
            CONNECTED_BINANCE_ACCOUNT["last_checked"] = datetime.now(timezone.utc).isoformat()

            settings.BINANCE_API_KEY = key
            settings.BINANCE_API_SECRET = secret
            settings.BINANCE_ENV = payload.environment

            return {
                "success": True,
                "connected": True,
                "environment": payload.environment,
                "api_key_masked": CONNECTED_BINANCE_ACCOUNT["api_key_masked"],
                "balances": real_balances,
                "message": "Binance Spot Testnet / Demo hesabınız başarıyla doğrulandı!",
            }
    except Exception as e:
        err_msg = str(e)
        logger.warning(f"Binance connection verification failed: {err_msg}")
        return {
            "success": False,
            "connected": False,
            "error": err_msg,
            "message": f"Binance Doğrulama Hatası: API Anahtarı veya Secret doğrulanamadı ({err_msg}).",
        }
