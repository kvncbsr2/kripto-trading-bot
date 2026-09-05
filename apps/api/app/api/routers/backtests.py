from dataclasses import asdict
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import apps.api.app.api.state as app_state
from apps.api.app.api.state import market_data_service
from services.backtest_engine.replay_engine import CausalHistoricalReplayEngine
from services.backtest_engine.vbt_backtest import VectorBTBacktester
from services.feature_engine.indicators.momentum import calculate_rsi
from shared.config import get_settings

router = APIRouter(tags=["backtests"])
settings = get_settings()

BACKTEST_RESULTS_CACHE: Dict[str, Dict[str, Any]] = {}


class BacktestRunRequest(BaseModel):
    symbol: str = "BTC/USDT"
    timeframe: str = "15m"
    strategy: str = "r10_rsi_divergence"
    initial_capital: float = 5000.0
    fees: float = 0.001
    slippage_bps: float = 5.0
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None


@router.post("/backtest/run")
@router.post("/api/v1/backtest/run")
@router.post("/api/backtest/run")
async def post_run_backtest(payload: BacktestRunRequest):
    """
    Executes VectorBT backtest on REAL Binance historical candle data.
    Maps timeframe dynamically. Zero synthetic price injection.
    """
    norm_symbol = payload.symbol.replace("-", "/").upper()
    candles = await market_data_service.get_historical_klines(
        norm_symbol,
        timeframe=payload.timeframe,
        limit=500,
        start=payload.start_time,
        end=payload.end_time,
    )
    if not candles or len(candles) < 50:
        raise HTTPException(
            status_code=422,
            detail=f"DATA_UNAVAILABLE: Insufficient Binance historical candles ({len(candles) if candles else 0} < 50 required) for {norm_symbol}. Zero synthetic fallback allowed.",
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
    df.set_index("timestamp", inplace=True)

    # Generate signals based on strategy
    rsi = calculate_rsi(df["close"], 14)
    entries = (rsi.shift(1) < 35) & (rsi > rsi.shift(1))
    exits = rsi > 65

    backtester = VectorBTBacktester(
        initial_capital=payload.initial_capital,
        fees=payload.fees,
        slippage_bps=payload.slippage_bps,
    )

    result = backtester.run_backtest_from_signals(
        close=df["close"],
        entries=entries,
        exits=exits,
        timeframe=payload.timeframe,
    )
    result["symbol"] = norm_symbol
    result["timeframe"] = payload.timeframe
    result["candles_tested"] = len(df)
    result["run_at"] = datetime.now(timezone.utc).isoformat()

    run_id = f"bt_{norm_symbol.replace('/', '_')}_{payload.timeframe}_{int(datetime.now(timezone.utc).timestamp())}"
    BACKTEST_RESULTS_CACHE[run_id] = result
    result["run_id"] = run_id
    return result


@router.get("/backtest/results")
@router.get("/api/v1/backtest/results")
@router.get("/api/backtest/results")
async def get_backtest_results():
    return list(BACKTEST_RESULTS_CACHE.values())


@router.get("/backtest/compare")
@router.get("/api/v1/backtest/compare")
@router.get("/api/backtest/compare")
async def get_backtest_compare():
    return {
        "runs_count": len(BACKTEST_RESULTS_CACHE),
        "comparisons": list(BACKTEST_RESULTS_CACHE.values()),
    }


@router.post("/api/v1/r10/replay")
async def post_run_r10_replay(symbol: str = "BTC/USDT"):
    """
    Executes Causal Historical Replay Engine:
    Fetches real historical Binance 1D candles and replays bar-by-bar with zero lookahead.
    If real Binance data is unavailable, returns DATA_UNAVAILABLE (Zero Fake Data Policy).
    """
    norm_symbol = symbol.replace("-", "/").upper()
    candles = await market_data_service.get_historical_klines(
        norm_symbol, timeframe="1d", limit=365
    )

    if not candles or len(candles) < 50:
        return {
            "status": "DATA_UNAVAILABLE",
            "symbol": norm_symbol,
            "message": "Gerçek Binance günlük verisi alınamadı. Sahte veri ile test yapılması engellendi (Zero Fake Data Policy).",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

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

    replay_engine = CausalHistoricalReplayEngine(initial_capital=settings.INITIAL_CAPITAL)
    report = replay_engine.run_replay(df, symbol=norm_symbol)
    report_dict = asdict(report)
    app_state.LATEST_REPLAY_REPORT = report_dict
    return report_dict


@router.get("/api/v1/r10/replay/report")
async def get_r10_replay_report():
    """Fetches the latest completed Causal Replay validation report."""
    if not app_state.LATEST_REPLAY_REPORT:
        return await post_run_r10_replay("BTC/USDT")
    return app_state.LATEST_REPLAY_REPORT
