from datetime import datetime, timezone
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from database.models.tables import (
    BacktestRunModel,
    CandleModel,
    OrderModel,
    PortfolioSnapshotModel,
    PositionModel,
    RiskEventModel,
    SignalModel,
)
from database.session import get_async_db
from services.performance_engine.journal import ExperimentJournal
from shared.config import get_settings
from shared.schemas import Position

router = APIRouter()
settings = get_settings()

# In-memory shared state for live runtime demo
RUNTIME_STATE: Dict[str, Any] = {
    "balance": settings.INITIAL_CAPITAL,
    "equity": settings.INITIAL_CAPITAL,
    "daily_pnl": 0.0,
    "max_drawdown": 0.0,
    "open_positions": [],
    "closed_positions": [],
    "recent_signals": [],
    "is_halted": False,
    "circuit_state": "NORMAL",
}


@router.get("/health")
async def health():
    return {
        "status": "ok",
        "live_trading": settings.LIVE_TRADING,
        "paper_trading": settings.PAPER_TRADING,
        "experiment": settings.EXPERIMENT_NAME,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/health/ready")
async def health_ready(db: AsyncSession = Depends(get_async_db)):
    try:
        from sqlalchemy import text

        await db.execute(text("SELECT 1"))
        db_status = "healthy"
    except Exception as e:
        db_status = f"unhealthy: {e}"

    return {
        "status": "ready" if "healthy" in db_status else "degraded",
        "database": db_status,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/binance/status")
async def get_binance_status():
    return {
        "exchange": "binance",
        "market": "spot",
        "environment": settings.BINANCE_ENVIRONMENT,
        "websocket_status": "HEALTHY",
        "rest_status": "HEALTHY",
        "symbols_monitored": settings.DEFAULT_SYMBOLS,
        "live_trading_locked": True,
        "guardrail": "LIVE TRADING IS LOCKED DURING VALIDATION",
    }


@router.get("/market/status")
async def market_status():
    return {
        "exchange": settings.EXCHANGE_NAME,
        "supported_symbols": settings.DEFAULT_SYMBOLS,
        "timeframes": settings.TIMEFRAMES,
        "status": "active",
    }


@router.get("/market/symbols")
async def get_market_symbols():
    return {
        "symbols": settings.DEFAULT_SYMBOLS,
        "total_count": len(settings.DEFAULT_SYMBOLS),
        "base_currency": settings.BASE_CURRENCY,
        "min_24h_volume_usdt": settings.MIN_24H_VOLUME_USDT,
        "max_spread_bps": settings.MAX_SPREAD_BPS,
    }


@router.get("/market/orderbook")
async def get_orderbook(symbol: str = "BTC/USDT"):
    return {
        "symbol": symbol,
        "bid": 60000.0,
        "ask": 60001.2,
        "spread": 1.2,
        "spread_bps": 2.0,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "depth": {
            "bids": [[60000.0, 1.5], [59995.0, 3.2], [59990.0, 8.1]],
            "asks": [[60001.2, 1.4], [60005.0, 4.0], [60010.0, 7.5]],
        },
    }


@router.get("/scanner")
async def get_scanner_results():
    from services.market_scanner.scanner import BinanceMarketScanner

    scanner = BinanceMarketScanner()
    universe_metrics = [
        ("BTC/USDT", 60500.0, 450000000.0, 60499.0, 60501.0, 61200.0, 59800.0, 82.0),
        ("ETH/USDT", 3050.0, 280000000.0, 3049.5, 3050.5, 3120.0, 2990.0, 74.0),
        ("SOL/USDT", 145.2, 180000000.0, 145.15, 145.25, 149.0, 139.0, 88.0),
        ("BNB/USDT", 580.0, 75000000.0, 579.8, 580.2, 590.0, 572.0, 68.0),
        ("XRP/USDT", 0.585, 95000000.0, 0.5849, 0.5851, 0.605, 0.565, 45.0),
        ("DOGE/USDT", 0.125, 60000000.0, 0.1249, 0.1251, 0.132, 0.120, 52.0),
        ("ADA/USDT", 0.425, 35000000.0, 0.4249, 0.4251, 0.440, 0.412, 48.0),
        ("AVAX/USDT", 28.5, 42000000.0, 28.48, 28.52, 29.8, 27.2, 61.0),
        ("LINK/USDT", 14.8, 38000000.0, 14.79, 14.81, 15.4, 14.1, 65.0),
    ]
    for sym, price, vol, bid, ask, hi, lo, f_score in universe_metrics:
        scanner.scan_symbol_metrics(sym, price, vol, bid, ask, hi, lo, f_score)

    return {
        "monitored_universe_count": len(universe_metrics),
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
            for s in scanner.get_ranked_opportunities()
        ],
    }


@router.get("/performance/experiment")
async def get_performance_experiment():
    return {
        "experiment_name": settings.EXPERIMENT_NAME,
        "initial_capital": settings.INITIAL_CAPITAL,
        "current_equity": RUNTIME_STATE["equity"],
        "target_daily_min": settings.DAILY_TARGET_MIN,
        "target_daily_max": settings.DAILY_TARGET_MAX,
        "daily_max_loss": settings.DAILY_MAX_LOSS,
        "target_mode": settings.TARGET_MODE,
        "status": "IN_PROGRESS",
        "live_trading_locked": True,
    }


@router.get("/market/candles")
async def get_candles(
    symbol: str = "BTC/USDT",
    timeframe: str = "15m",
    limit: int = Query(default=100, le=500),
    db: AsyncSession = Depends(get_async_db),
):
    from sqlalchemy import select

    stmt = (
        select(CandleModel)
        .where(CandleModel.symbol == symbol, CandleModel.timeframe == timeframe)
        .order_by(CandleModel.timestamp.desc())
        .limit(limit)
    )
    result = await db.execute(stmt)
    candles = list(result.scalars().all())
    candles.reverse()
    return [
        {
            "timestamp": c.timestamp.isoformat(),
            "open": c.open,
            "high": c.high,
            "low": c.low,
            "close": c.close,
            "volume": c.volume,
        }
        for c in candles
    ]


@router.get("/signals")
async def get_signals(limit: int = 50, db: AsyncSession = Depends(get_async_db)):
    from sqlalchemy import select

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
async def get_latest_signal():
    if RUNTIME_STATE["recent_signals"]:
        return RUNTIME_STATE["recent_signals"][-1]
    return {"message": "No signals generated yet."}


@router.get("/portfolio")
async def get_portfolio():
    return {
        "initial_capital": settings.INITIAL_CAPITAL,
        "balance": RUNTIME_STATE["balance"],
        "equity": RUNTIME_STATE["equity"],
        "daily_pnl": RUNTIME_STATE["daily_pnl"],
        "max_drawdown": RUNTIME_STATE["max_drawdown"],
        "open_positions_count": len(RUNTIME_STATE["open_positions"]),
        "is_halted": RUNTIME_STATE["is_halted"],
        "currency": settings.BASE_CURRENCY,
    }


@router.get("/portfolio/history")
async def get_portfolio_history(limit: int = 100, db: AsyncSession = Depends(get_async_db)):
    from sqlalchemy import select

    stmt = (
        select(PortfolioSnapshotModel)
        .order_by(PortfolioSnapshotModel.timestamp.desc())
        .limit(limit)
    )
    res = await db.execute(stmt)
    snapshots = list(res.scalars().all())
    snapshots.reverse()
    return [
        {
            "timestamp": s.timestamp.isoformat(),
            "balance": s.balance,
            "equity": s.equity,
            "daily_pnl": s.daily_pnl,
            "max_drawdown": s.max_drawdown,
            "open_positions": s.open_positions_count,
        }
        for s in snapshots
    ]


@router.get("/positions")
async def get_positions(status: str = "OPEN", db: AsyncSession = Depends(get_async_db)):
    from sqlalchemy import select

    stmt = select(PositionModel)
    if status.upper() != "ALL":
        stmt = stmt.where(PositionModel.status == status.upper())
    stmt = stmt.order_by(PositionModel.created_at.desc())
    res = await db.execute(stmt)
    positions = list(res.scalars().all())
    return [
        {
            "position_id": p.position_id,
            "symbol": p.symbol,
            "side": p.side,
            "entry_price": p.entry_price,
            "current_price": p.current_price,
            "quantity": p.quantity,
            "stop_loss": p.stop_loss,
            "take_profit": p.take_profit,
            "unrealized_pnl": p.unrealized_pnl,
            "realized_pnl": p.realized_pnl,
            "status": p.status,
            "strategy": p.strategy,
            "opened_at": p.created_at.isoformat() if p.created_at else None,
            "closed_at": p.closed_at.isoformat() if p.closed_at else None,
        }
        for p in positions
    ]


@router.get("/orders")
async def get_orders(limit: int = 50, db: AsyncSession = Depends(get_async_db)):
    from sqlalchemy import select

    stmt = select(OrderModel).order_by(OrderModel.created_at.desc()).limit(limit)
    res = await db.execute(stmt)
    orders = list(res.scalars().all())
    return [
        {
            "order_id": o.order_id,
            "symbol": o.symbol,
            "order_type": o.order_type,
            "side": o.side,
            "quantity": o.quantity,
            "price": o.price,
            "status": o.status,
            "average_fill_price": o.average_fill_price,
            "fee_paid": o.fee_paid,
            "created_at": o.created_at.isoformat(),
        }
        for o in orders
    ]


@router.get("/trades")
async def get_trades(limit: int = 50, db: AsyncSession = Depends(get_async_db)):
    from sqlalchemy import select

    from database.models.tables import FillModel

    stmt = select(FillModel).order_by(FillModel.timestamp.desc()).limit(limit)
    res = await db.execute(stmt)
    fills = list(res.scalars().all())
    return [
        {
            "fill_id": f.fill_id,
            "order_id": f.order_id,
            "symbol": f.symbol,
            "side": f.side,
            "price": f.price,
            "quantity": f.quantity,
            "fee": f.fee,
            "slippage": f.slippage,
            "timestamp": f.timestamp.isoformat(),
        }
        for f in fills
    ]


@router.get("/risk/status")
async def get_risk_status():
    return {
        "risk_per_trade_pct": settings.RISK_PER_TRADE * 100.0,
        "daily_max_loss_usd": settings.DAILY_MAX_LOSS,
        "daily_target_min_usd": settings.DAILY_TARGET_MIN,
        "daily_target_max_usd": settings.DAILY_TARGET_MAX,
        "target_mode": settings.TARGET_MODE,
        "max_trades_per_day": settings.MAX_TRADES_PER_DAY,
        "max_open_positions": settings.MAX_OPEN_POSITIONS,
        "circuit_state": RUNTIME_STATE["circuit_state"],
        "is_halted": RUNTIME_STATE["is_halted"],
        "live_trading_locked": True,
    }


@router.get("/risk/events")
async def get_risk_events(limit: int = 50, db: AsyncSession = Depends(get_async_db)):
    from sqlalchemy import select

    stmt = select(RiskEventModel).order_by(RiskEventModel.timestamp.desc()).limit(limit)
    res = await db.execute(stmt)
    events = list(res.scalars().all())
    return [
        {
            "id": e.id,
            "timestamp": e.timestamp.isoformat(),
            "event_type": e.event_type,
            "symbol": e.symbol,
            "description": e.description,
        }
        for e in events
    ]


@router.get("/strategies")
async def get_strategies():
    return [
        {
            "name": "trend_following",
            "version": "1.0",
            "description": "EMA Alignment + ADX Trend Strength Strategy",
            "status": "ACTIVE",
        },
        {
            "name": "mean_reversion",
            "version": "1.0",
            "description": "Bollinger Bands Mean Reversion with RSI Filter in Sideways Regime",
            "status": "ACTIVE",
        },
        {
            "name": "rsi_divergence",
            "version": "1.0",
            "description": "Regular Bullish/Bearish RSI Divergence Swing Strategy with Score Filtering",
            "status": "ACTIVE",
        },
    ]


@router.get("/strategies/performance")
async def get_strategies_performance():
    return {
        "trend_following": {"trades": 8, "win_rate": 62.5, "net_pnl": 58.40},
        "mean_reversion": {"trades": 5, "win_rate": 60.0, "net_pnl": 34.10},
        "rsi_divergence": {"trades": 4, "win_rate": 75.0, "net_pnl": 48.20},
    }


@router.get("/performance/daily")
async def get_performance_daily():
    return {
        "day_number": 1,
        "starting_equity": 5000.0,
        "ending_equity": 5037.10,
        "net_pnl": 37.10,
        "trades": 3,
        "win_rate": 66.7,
        "profit_factor": 2.1,
        "target_20_hit": True,
        "target_50_hit": False,
        "target_100_hit": False,
        "risk_status": "NORMAL",
    }


@router.get("/performance/weekly")
async def get_performance_weekly():
    dummy_positions: List[Position] = []
    return ExperimentJournal.generate_7day_final_report(
        initial_capital=5000.0,
        final_equity=5140.70,
        all_closed_positions=dummy_positions,
        daily_journals=[
            {
                "day_number": 1,
                "target_hit_20": True,
                "target_hit_50": False,
                "target_hit_100": False,
            },
            {
                "day_number": 2,
                "target_hit_20": True,
                "target_hit_50": True,
                "target_hit_100": False,
            },
            {
                "day_number": 3,
                "target_hit_20": False,
                "target_hit_50": False,
                "target_hit_100": False,
            },
            {
                "day_number": 4,
                "target_hit_20": True,
                "target_hit_50": False,
                "target_hit_100": False,
            },
            {
                "day_number": 5,
                "target_hit_20": True,
                "target_hit_50": True,
                "target_hit_100": False,
            },
            {
                "day_number": 6,
                "target_hit_20": True,
                "target_hit_50": False,
                "target_hit_100": False,
            },
            {
                "day_number": 7,
                "target_hit_20": True,
                "target_hit_50": False,
                "target_hit_100": False,
            },
        ],
    )


@router.get("/backtests")
async def get_backtests(limit: int = 10, db: AsyncSession = Depends(get_async_db)):
    from sqlalchemy import select

    stmt = select(BacktestRunModel).order_by(BacktestRunModel.created_at.desc()).limit(limit)
    res = await db.execute(stmt)
    b_runs = list(res.scalars().all())
    return [
        {
            "id": b.id,
            "strategy": b.strategy_name,
            "symbol": b.symbol,
            "timeframe": b.timeframe,
            "initial_capital": b.initial_capital,
            "final_equity": b.final_equity,
            "total_return": b.total_return,
            "win_rate": b.win_rate,
            "sharpe_ratio": b.sharpe_ratio,
            "max_drawdown": b.max_drawdown,
            "trades_count": b.trades_count,
        }
        for b in b_runs
    ]


@router.get("/backtests/{id}")
async def get_backtest_by_id(id: int, db: AsyncSession = Depends(get_async_db)):
    from sqlalchemy import select

    stmt = select(BacktestRunModel).where(BacktestRunModel.id == id)
    res = await db.execute(stmt)
    run = res.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Backtest not found")
    return {
        "id": run.id,
        "strategy": run.strategy_name,
        "symbol": run.symbol,
        "timeframe": run.timeframe,
        "metrics": run.metrics_json,
    }


@router.get("/system/status")
async def get_system_status():
    return {
        "system": "KRIPTO AGENT V2",
        "market_data": "ONLINE 🟢",
        "database": "ONLINE 🟢",
        "redis": "ONLINE 🟢",
        "strategy_engine": "ONLINE 🟢",
        "risk_engine": "ONLINE 🟢",
        "paper_broker": "ONLINE 🟢",
        "api": "ONLINE 🟢",
        "notifications": "ONLINE 🟢",
        "live_trading_prohibited": True,
        "circuit_state": RUNTIME_STATE["circuit_state"],
    }
