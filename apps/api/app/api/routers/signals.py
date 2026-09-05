from datetime import datetime, timezone

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.api.state import RUNTIME_STATE, market_data_service, risk_engine
from apps.api.app.middleware.auth import Role, verify_api_key_or_token
from database.models.tables import SignalModel
from database.session import get_async_db
from services.strategy_engine.strategies.r10_rsi_divergence import create_r10_strategy_from_settings
from shared.enums import SignalDirection

router = APIRouter(tags=["signals"])
r10_strategy = create_r10_strategy_from_settings()


class GenerateSignalRequest(BaseModel):
    symbol: str = "BTC/USDT"
    timeframe: str = "15m"


@router.get("/signals")
@router.get("/signals/history")
async def get_signals(limit: int = 50, db: AsyncSession = Depends(get_async_db)):
    """Fetches real historical signals recorded in the database."""
    stmt = select(SignalModel).order_by(SignalModel.timestamp.desc()).limit(limit)
    res = await db.execute(stmt)
    db_signals = list(res.scalars().all())
    return [
        {
            "id": s.id,
            "symbol": s.symbol,
            "strategy": s.strategy,
            "direction": s.direction,
            "entry_price": s.entry_price,
            "stop_price": s.stop_price,
            "take_profit": s.take_profit,
            "confidence": s.confidence,
            "regime": s.regime,
            "reason": s.reason,
            "timestamp": s.timestamp.isoformat(),
        }
        for s in db_signals
    ]


@router.get("/signals/latest")
@router.get("/signals/active")
async def get_active_signals():
    """Returns the most recent real signal or NO_DATA (Zero Fake Data)."""
    if RUNTIME_STATE.get("recent_signals"):
        return RUNTIME_STATE["recent_signals"][-1]
    return {"status": "NO_DATA", "message": "Henüz aktif sinyal bulunmuyor."}


@router.post("/signals/generate")
async def post_generate_signal(
    payload: GenerateSignalRequest,
    _role: Role = Depends(verify_api_key_or_token),
):
    """
    Evaluates real market candles for candidate symbol and generates strictly causal signal.
    """
    norm_symbol = payload.symbol.replace("-", "/").upper()
    candles = await market_data_service.get_historical_klines(
        norm_symbol, timeframe=payload.timeframe, limit=100
    )
    if not candles or len(candles) < 30:
        raise HTTPException(
            status_code=404,
            detail=f"Insufficient candle data for {norm_symbol} from Binance",
        )

    df = pd.DataFrame([
        {
            "timestamp": c.timestamp,
            "open": c.open,
            "high": c.high,
            "low": c.low,
            "close": c.close,
            "volume": c.volume,
        }
        for c in candles
    ])

    signal = r10_strategy.evaluate_from_dataframe(df, symbol=norm_symbol)
    if not signal:
        return {
            "symbol": norm_symbol,
            "has_signal": False,
            "message": "Strateji kriterlerine uygun sinyal tespit edilmedi.",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    is_spot_short = risk_engine.is_spot_mode and signal.direction == SignalDirection.SHORT
    signal_dict = signal.model_dump()
    signal_dict["is_executable"] = not is_spot_short
    signal_dict["execution_restriction"] = "SIGNAL_ONLY (Spot Mode)" if is_spot_short else "EXECUTABLE"

    RUNTIME_STATE.setdefault("recent_signals", []).append(signal_dict)
    return {
        "symbol": norm_symbol,
        "has_signal": True,
        "signal": signal_dict,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
