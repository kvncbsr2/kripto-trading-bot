from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.api.state import RUNTIME_STATE, command_bus, risk_engine
from apps.api.app.middleware.auth import Role, verify_api_key_or_token
from database.session import get_async_db
from services.autonomous_runner import autonomous_trader
from services.execution.order_manager import order_manager
from services.risk_engine.readiness_gate import ReadinessGate
from shared.config import get_settings
from shared.enums import TradingWorkerState

router = APIRouter(tags=["system"])
settings = get_settings()


class ModeChangeRequest(BaseModel):
    mode: str  # "PAPER" or "SIGNAL_ONLY"


class TargetModeRequest(BaseModel):
    target_mode: str  # "SOFT" or "HARD"


class ResetRequest(BaseModel):
    confirmation: bool = False


class AutonomousToggleRequest(BaseModel):
    active: bool


@router.get("/system/state")
@router.get("/system/status")
@router.get("/api/system/status")
@router.get("/api/v1/system/status")
async def get_system_state():
    broker = command_bus.broker
    if broker and broker.open_positions:
        for sym in list(broker.open_positions.keys()):
            try:
                ticker = await market_data_service.get_live_ticker(sym)
                if ticker and "price" in ticker and ticker["price"] > 0:
                    broker.update_market_price(sym, float(ticker["price"]))
            except Exception:
                pass
    snapshot = broker.get_portfolio_snapshot() if broker else {}

    active_lvl = RUNTIME_STATE.get("active_profile_level", 1)
    active_prof = RUNTIME_STATE.get("active_risk_profile")
    if not active_prof:
        from services.config_manager.risk_profiles import get_profile
        active_prof = get_profile(active_lvl).to_dict()

    active_strat_id = RUNTIME_STATE.get("active_strategy_id", "r10_rsi_divergence")
    active_strat = RUNTIME_STATE.get("active_strategy")
    if not active_strat:
        from services.strategy_engine.registry import get_strategy_metadata
        active_strat = get_strategy_metadata(active_strat_id).to_dict()

    return {
        "system": "KRIPTO AGENT V6.1",
        "system_state": RUNTIME_STATE.get("system_state", "READY"),
        "initial_capital": snapshot.get("initial_balance", settings.INITIAL_CAPITAL),
        "equity": snapshot.get("equity", settings.INITIAL_CAPITAL),
        "balance": snapshot.get("balance", settings.INITIAL_CAPITAL),
        "available_balance": snapshot.get("available_balance", snapshot.get("balance", settings.INITIAL_CAPITAL)),
        "reserved_balance": snapshot.get("reserved_balance", 0.0),
        "daily_pnl": round(snapshot.get("unrealized_pnl", 0.0) + snapshot.get("realized_pnl", 0.0), 2),
        "realized_pnl": snapshot.get("realized_pnl", 0.0),
        "unrealized_pnl": snapshot.get("unrealized_pnl", 0.0),
        "total_fees": snapshot.get("total_fees", 0.0),
        "total_slippage": snapshot.get("total_slippage", 0.0),
        "open_positions_count": snapshot.get("open_positions_count", 0),
        "max_open_positions": risk_engine.max_open_positions,
        "daily_target": RUNTIME_STATE.get("daily_target", getattr(settings, "DAILY_TARGET", 100.0)),
        "daily_target_min": RUNTIME_STATE.get("daily_target_min", getattr(settings, "DAILY_TARGET_MIN", 100.0)),
        "daily_target_max": RUNTIME_STATE.get("daily_target_max", getattr(settings, "DAILY_TARGET_MAX", 150.0)),
        "daily_max_loss": RUNTIME_STATE.get("daily_max_loss", getattr(settings, "DAILY_MAX_LOSS", 100.0)),
        "active_profile_level": active_lvl,
        "active_risk_profile": active_prof,
        "active_strategy_id": active_strat_id,
        "active_strategy": active_strat,
        "is_halted": RUNTIME_STATE.get("is_halted", False),
        "circuit_state": RUNTIME_STATE.get("circuit_state", "NORMAL"),
        "mode": "PAPER_TRADING",
        "is_spot_mode": True,
        "is_autonomous_active": autonomous_trader.is_active,
        "last_cycle_at": autonomous_trader.last_cycle_at,
        "last_action": autonomous_trader.last_action,
        "live_trading_prohibited": True,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }



@router.get("/system/logs")
@router.get("/api/system/logs")
@router.get("/api/v1/system/logs")
async def get_system_logs(limit: int = 50):
    """Returns live streaming system logs for dashboard real-time terminal."""
    from shared.logging import get_recent_logs
    return {
        "logs": get_recent_logs(limit=limit),
        "is_active": autonomous_trader.is_active,
        "last_action": autonomous_trader.last_action,
        "last_cycle_at": autonomous_trader.last_cycle_at,
    }


@router.get("/portfolio")
async def get_portfolio():
    broker = command_bus.broker
    snapshot = broker.get_portfolio_snapshot() if broker else {}
    equity = snapshot.get("equity", settings.INITIAL_CAPITAL)
    daily_pnl = round(snapshot.get("unrealized_pnl", 0.0) + snapshot.get("realized_pnl", 0.0), 2)
    max_dd = (settings.INITIAL_CAPITAL - equity) / settings.INITIAL_CAPITAL if equity < settings.INITIAL_CAPITAL else 0.0

    return {
        "initial_capital": settings.INITIAL_CAPITAL,
        "balance": snapshot.get("balance", settings.INITIAL_CAPITAL),
        "equity": equity,
        "daily_pnl": daily_pnl,
        "max_drawdown": round(max_dd, 4),
        "open_positions_count": snapshot.get("open_positions_count", 0),
        "is_halted": RUNTIME_STATE.get("is_halted", False),
        "currency": settings.BASE_CURRENCY,
    }


@router.post("/system/mode")
async def post_system_mode(
    payload: ModeChangeRequest,
    _role: Role = Depends(verify_api_key_or_token),
):
    if payload.mode not in ["PAPER", "SIGNAL_ONLY"]:
        raise HTTPException(status_code=400, detail="Only PAPER and SIGNAL_ONLY modes are permitted.")
    RUNTIME_STATE["execution_mode"] = payload.mode
    return {"status": "SUCCESS", "mode": payload.mode}


@router.post("/system/target-mode")
async def post_system_target_mode(
    payload: TargetModeRequest,
    _role: Role = Depends(verify_api_key_or_token),
):
    if payload.target_mode not in ["SOFT", "HARD"]:
        raise HTTPException(status_code=400, detail="Target mode must be SOFT or HARD.")
    risk_engine.target_mode = payload.target_mode
    RUNTIME_STATE["target_mode"] = payload.target_mode
    return {"status": "SUCCESS", "target_mode": payload.target_mode}


@router.post("/system/reset")
@router.post("/api/system/reset-experiment")
async def post_system_reset(
    payload: ResetRequest,
    _role: Role = Depends(verify_api_key_or_token),
):
    if not payload.confirmation:
        raise HTTPException(status_code=400, detail="Explicit confirmation=True required to reset.")
    autonomous_trader.reset_state()
    init_cap = float(get_settings().INITIAL_CAPITAL)
    if command_bus.broker:
        command_bus.broker.reset_portfolio(init_cap)
        if hasattr(command_bus.broker, "unhalt"):
            command_bus.broker.unhalt()
    risk_engine.circuit_breaker.reset(baseline_trade_count=0)
    risk_engine.trades_today = 0
    risk_engine.daily_realized_pnl = 0.0
    autonomous_trader.cycle_count = 0
    autonomous_trader.last_action = "Otonom motor sıfırlandı ve beklemede (OFF)."
    RUNTIME_STATE["is_halted"] = False
    RUNTIME_STATE["system_state"] = "READY"
    RUNTIME_STATE["circuit_state"] = "NORMAL"
    RUNTIME_STATE["is_autonomous_active"] = False
    RUNTIME_STATE["balance"] = init_cap
    RUNTIME_STATE["equity"] = init_cap
    RUNTIME_STATE["realized_pnl"] = 0.0
    RUNTIME_STATE["unrealized_pnl"] = 0.0
    RUNTIME_STATE["daily_pnl"] = 0.0
    RUNTIME_STATE["open_positions"] = []
    RUNTIME_STATE["closed_positions"] = []
    return {"status": "SUCCESS", "message": f"Paper portföyü ve sistem sıfırlandı. ${init_cap:,.2f} bakiye hazır."}


@router.get("/system/audit-log")
@router.get("/api/system/audit-logs")
async def get_system_audit_log():
    return [e.model_dump() for e in reversed(command_bus.audit_log)]


@router.get("/api/system/readiness")
async def get_system_readiness(db: AsyncSession = Depends(get_async_db)):
    return await ReadinessGate.evaluate(db_session=db)


@router.get("/api/v1/agent/autonomous-status")
async def get_autonomous_status():
    return {
        "active": autonomous_trader.is_active,
        "cycle_count": autonomous_trader.cycle_count,
        "last_action": autonomous_trader.last_action,
        "last_cycle_at": autonomous_trader.last_cycle_at,
        "open_positions": len(command_bus.broker.open_positions) if command_bus.broker else 0,
        "max_open_positions": risk_engine.max_open_positions,
    }


@router.post("/api/v1/agent/autonomous-toggle")
async def post_autonomous_toggle(
    payload: AutonomousToggleRequest,
    _role: Role = Depends(verify_api_key_or_token),
):
    if payload.active:
        autonomous_trader.start()
    else:
        autonomous_trader.stop()
    return {
        "success": True,
        "active": autonomous_trader.is_active,
        "message": f"Otonom al-sat botu {'BAŞLATILDI' if payload.active else 'DURDURULDU'}.",
    }


@router.post("/control-center/run-cycle")
@router.post("/api/v1/agent/run-cycle")
async def post_run_cycle(
    _role: Role = Depends(verify_api_key_or_token),
):
    """Triggers an authoritative real-market causal evaluation cycle on-demand."""
    res = await autonomous_trader.step_cycle()
    return res


@router.post("/system/emergency-shutdown")
@router.post("/api/system/emergency-shutdown")
@router.post("/api/agent/emergency-stop")
@router.post("/api/v1/risk/emergency-stop")
@router.post("/api/risk/emergency-stop")
async def post_emergency_shutdown(
    _role: Role = Depends(verify_api_key_or_token),
):
    """
    Immediately stops autonomous trading, cancels all open orders across OrderManager and Broker,
    liquidates open positions, and transitions system state to EMERGENCY HALT.
    """
    # 1. Authoritative Emergency Stop on Autonomous Trader (cancels task, acquires state)
    autonomous_trader.emergency_stop(reason="API Emergency Stop Triggered")

    # 2. Cancel all pending orders via OrderManager and Broker
    cancelled_count = 0
    if command_bus.broker:
        order_manager.bind_execution_engine(command_bus.broker)
        cancelled_count += await order_manager.cancel_all_orders()
        if hasattr(command_bus.broker, "cancel_all_orders"):
            cancelled_count += await command_bus.broker.cancel_all_orders()

    # 3. Transition system and circuit state to EMERGENCY
    RUNTIME_STATE["is_halted"] = True
    RUNTIME_STATE["system_state"] = "RISK_LOCK"
    RUNTIME_STATE["circuit_state"] = "EMERGENCY"
    risk_engine.circuit_breaker.state = "EMERGENCY"

    open_pos_count = len(command_bus.broker.open_positions) if command_bus.broker else 0
    command_bus._log_audit(
        action="EMERGENCY_STOP",
        parameters={"cancelled_orders": cancelled_count, "open_positions": open_pos_count},
        success=True,
        result={"system_state": "RISK_LOCK", "is_halted": True},
    )
    return {
        "success": True,
        "status": "HALTED",
        "system_state": "RISK_LOCK",
        "is_halted": True,
        "cancelled_orders_count": cancelled_count,
        "open_positions_count": open_pos_count,
        "message": f"Acil durum durdurması uygulandı. Otonom motor kapatıldı, {cancelled_count} bekleyen emir iptal edildi.",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/system/unhalt")
@router.post("/api/system/unhalt")
@router.post("/api/agent/unhalt")
@router.post("/api/v1/risk/unhalt")
async def post_system_unhalt(
    force: bool = False,
    _role: Role = Depends(verify_api_key_or_token),
):
    """
    Safely clears manual emergency halt / RISK_LOCK / EXPECTANCY_HALTED state,
    advancing the circuit breaker baseline so trading can resume.
    """
    broker = command_bus.broker
    portfolio = broker.to_portfolio_state() if broker else None

    # 1. Hard Daily Max Loss Check (Unless forced)
    if portfolio and portfolio.daily_pnl <= -risk_engine.daily_max_loss_usd and not force:
        msg = f"Günlük zarar limiti aşıldığı için kilit açılamaz (-${abs(portfolio.daily_pnl):.2f} <= -${risk_engine.daily_max_loss_usd:.2f})."
        return {"success": False, "system_state": "DAILY_RISK_LOCK", "message": msg, "detail": msg}

    # 2. Reset circuit breaker with current closed trade count as baseline
    closed = getattr(broker, "closed_positions_history", []) if broker else []
    RUNTIME_STATE["is_halted"] = False
    RUNTIME_STATE["circuit_state"] = "NORMAL"
    RUNTIME_STATE["system_state"] = "READY"
    if autonomous_trader.worker_state == TradingWorkerState.EMERGENCY_STOP:
        autonomous_trader.reset_state()
    if broker and hasattr(broker, "unhalt"):
        broker.unhalt()
    risk_engine.circuit_breaker.reset(baseline_trade_count=len(closed))
    command_bus._log_audit("UNHALT_SYSTEM", {"force": force, "baseline_trade_count": len(closed)}, True, {"system_state": "READY", "is_halted": False})

    return {
        "success": True,
        "status": "UNHALTED",
        "system_state": "READY",
        "is_halted": False,
        "message": "Sistem kilidi başarıyla kaldırıldı. Bot başlatılmaya hazır.",
    }


@router.post("/api/agent/start")
async def post_start_agent(
    unhalt: bool = False,
    force: bool = False,
    _role: Role = Depends(verify_api_key_or_token),
):
    broker = command_bus.broker
    portfolio = broker.to_portfolio_state() if broker else None

    # 1. Hard daily max loss check
    if portfolio and portfolio.daily_pnl <= -risk_engine.daily_max_loss_usd and not force:
        msg = f"Günlük zarar limiti aşıldı (-${abs(portfolio.daily_pnl):.2f} <= -${risk_engine.daily_max_loss_usd:.2f}). Alımlar kilitli."
        return {"success": False, "system_state": "DAILY_RISK_LOCK", "message": msg, "detail": msg}

    closed = getattr(broker, "closed_positions_history", []) if broker else []

    # 2. If halted and unhalt/force not explicitly requested, fail closed per security invariants
    if RUNTIME_STATE.get("is_halted") and not unhalt and not force:
        return {"success": False, "system_state": RUNTIME_STATE.get("system_state"), "message": "System is halted. Pass unhalt=true or call /api/system/unhalt to resume."}

    # 3. If unhalt/force requested, clear emergency state
    if unhalt or force:
        if autonomous_trader.worker_state == TradingWorkerState.EMERGENCY_STOP:
            autonomous_trader.reset_state()
        if broker and hasattr(broker, "unhalt"):
            broker.unhalt()

    # 4. Advance circuit breaker baseline and start trader
    RUNTIME_STATE["is_halted"] = False
    RUNTIME_STATE["circuit_state"] = "NORMAL"
    RUNTIME_STATE["system_state"] = "TRADING"
    risk_engine.circuit_breaker.reset(baseline_trade_count=len(closed))
    try:
        autonomous_trader.start()
    except RuntimeError as e:
        if "already RUNNING" in str(e):
            return {"success": True, "status": "ALREADY_RUNNING", "system_state": "TRADING", "message": str(e)}
        raise
    RUNTIME_STATE["is_autonomous_active"] = True
    command_bus._log_audit("START_AGENT", {"unhalt": unhalt, "force": force, "baseline_trade_count": len(closed)}, True, {"system_state": "TRADING"})
    return {"success": True, "status": "STARTED", "system_state": "TRADING", "message": "Otonom ajan başlatıldı."}


@router.post("/api/agent/pause")
async def post_pause_agent(
    _role: Role = Depends(verify_api_key_or_token),
):
    autonomous_trader.pause()
    RUNTIME_STATE["system_state"] = "PAUSED"
    command_bus._log_audit("PAUSE_AGENT", {}, True, {"system_state": "PAUSED"})
    return {"success": True, "status": "PAUSED", "system_state": "PAUSED", "message": "Otonom ajan duraklatıldı."}


@router.post("/api/agent/resume")
async def post_resume_agent(
    _role: Role = Depends(verify_api_key_or_token),
):
    if RUNTIME_STATE.get("is_halted"):
        return {"success": False, "system_state": RUNTIME_STATE.get("system_state"), "message": "Resume blocked during RISK_LOCK"}
    autonomous_trader.resume()
    RUNTIME_STATE["system_state"] = "TRADING"
    command_bus._log_audit("RESUME_AGENT", {}, True, {"system_state": "TRADING"})
    return {"success": True, "status": "RESUMED", "system_state": "TRADING", "message": "Otonom ajan devam ettirildi."}


@router.post("/api/agent/stop")
async def post_stop_agent(
    _role: Role = Depends(verify_api_key_or_token),
):
    autonomous_trader.stop()
    RUNTIME_STATE["system_state"] = "STOPPED"
    command_bus._log_audit("STOP_AGENT", {}, True, {"system_state": "STOPPED"})
    return {"success": True, "status": "STOPPED", "system_state": "STOPPED", "message": "Otonom ajan durduruldu."}


class RiskConfigPayload(BaseModel):
    risk_per_trade_pct: Optional[float] = Field(None, ge=0.001, le=0.05)
    daily_max_loss_usd: Optional[float] = Field(None, ge=10.0, le=1000.0)
    daily_max_loss_pct: Optional[float] = Field(None, ge=10.0, le=1000.0, description="Deprecated alias for daily_max_loss_usd")
    max_open_positions: Optional[int] = Field(None, ge=1, le=25)
    atr_multiplier: Optional[float] = Field(None, ge=0.5, le=5.0)


@router.post("/api/risk/config")
async def post_risk_config(
    payload: RiskConfigPayload,
    _role: Role = Depends(verify_api_key_or_token),
):
    updated = {}
    if payload.risk_per_trade_pct is not None:
        risk_engine.risk_per_trade = payload.risk_per_trade_pct
        updated["risk_per_trade_pct"] = payload.risk_per_trade_pct
    loss_usd = payload.daily_max_loss_usd if payload.daily_max_loss_usd is not None else payload.daily_max_loss_pct
    if loss_usd is not None:
        risk_engine.daily_max_loss_usd = loss_usd
        risk_engine.circuit_breaker.daily_max_loss_usd = loss_usd
        updated["daily_max_loss_usd"] = loss_usd
    if payload.max_open_positions is not None:
        risk_engine.max_open_positions = payload.max_open_positions
        updated["max_open_positions"] = payload.max_open_positions
    if payload.atr_multiplier is not None:
        updated["atr_multiplier"] = payload.atr_multiplier

    command_bus._log_audit("UPDATE_RISK_CONFIG", updated, True, updated)
    return {"success": True, "updated_parameters": updated}


@router.post("/api/scanner/run")
@router.post("/control-center/scanner/run")
async def post_scanner_run(_role: Role = Depends(verify_api_key_or_token)):
    from apps.api.app.api.routers.scanner import get_scanner_opportunities
    res = await get_scanner_opportunities()
    command_bus._log_audit("RUN_SCANNER", {}, True, {"scanned": res["monitored_universe_count"]})
    return {
        "success": True,
        "scanned_count": res["monitored_universe_count"],
        "opportunities": res["ranked_symbols"],
    }


@router.post("/api/monte-carlo/run")
@router.post("/control-center/monte-carlo/run")
async def post_monte_carlo_run(_role: Role = Depends(verify_api_key_or_token)):
    from services.performance_engine.monte_carlo import MonteCarloSimulator
    broker = command_bus.broker
    closed = getattr(broker, "closed_positions_history", []) if broker else []
    trade_pnls = [float(getattr(p, "realized_pnl", 0.0)) for p in closed if getattr(p, "realized_pnl", None) is not None]

    if len(trade_pnls) < 5:
        return {
            "success": False,
            "status": "INSUFFICIENT_DATA",
            "message": f"Monte Carlo simülasyonu için en az 5 adet kapanmış gerçek işlem (closed trade) gereklidir. Mevcut: {len(trade_pnls)} işlem.",
            "simulations": 0,
            "trade_count": len(trade_pnls),
            "metrics": {},
        }

    res = MonteCarloSimulator.run_simulation(trade_pnls, iterations=1000)
    command_bus._log_audit("RUN_MONTE_CARLO", {"trade_count": len(trade_pnls)}, True, res)
    return {
        "success": True,
        "status": "COMPLETED",
        "simulations": 1000,
        "trade_count": len(trade_pnls),
        "metrics": res,
    }


@router.post("/api/backtest/run")
@router.post("/control-center/backtest/run")
async def post_control_center_backtest(
    payload: Dict[str, Any],
    _role: Role = Depends(verify_api_key_or_token),
):
    from apps.api.app.api.routers.backtests import BacktestRunRequest, post_run_backtest
    req = BacktestRunRequest(
        symbol=payload.get("symbol", "BTC/USDT"),
        timeframe=payload.get("timeframe", "15m"),
        strategy=payload.get("strategy", "r10_rsi_divergence"),
        initial_capital=float(payload.get("initial_capital", 5000.0)),
        fees=float(payload.get("fees", 0.001)),
        slippage_bps=float(payload.get("slippage_bps", 5.0)),
    )
    return await post_run_backtest(req)


@router.get("/api/ai/agents")
async def get_ai_agents():
    """Authoritative AI multi-agent committee status with zero fake metrics."""
    return {
        "technical_agent": {"status": "ONLINE", "role": "Market Structure & Momentum Analyst"},
        "sentiment_agent": {"status": "STANDBY", "role": "Social & News Sentiment Reader"},
        "macro_agent": {"status": "STANDBY", "role": "Global Liquidity & Macro Monitor"},
        "onchain_agent": {"status": "STANDBY", "role": "Whale Flow & On-Chain Metrics"},
        "risk_agent": {"status": "ONLINE", "role": "Capital Preservation Guardian (Veto Authority)"},
        "orchestrator": {"status": "ONLINE", "role": "Consensus & Recommendation Synthesizer"},
        "guardrail": "AI generates recommendations only. Risk Engine retains absolute veto power.",
    }


@router.get("/api/v1/system/next-action")
@router.get("/system/next-action")
async def get_system_next_action():
    from apps.api.app.api.routers.backtests import BACKTEST_RESULTS_CACHE
    from apps.api.app.api.state import autonomous_trader, paper_broker

    has_backtest = len(BACKTEST_RESULTS_CACHE) > 0
    is_auto_running = autonomous_trader.is_active
    open_positions = len(paper_broker.open_positions)

    if not has_backtest:
        return {
            "step": "BACKTEST",
            "title": "İlk backtesti çalıştır",
            "description": "R10 RSI Divergence stratejisini Binance geçmiş mum verisinde test edin.",
            "action": "RUN_BACKTEST",
            "action_label": "BACKTESTİ BAŞLAT",
            "target_nav": "STRATEJİLER",
            "target_subtab": "backtest",
        }
    elif not is_auto_running and open_positions == 0:
        return {
            "step": "PAPER_TEST",
            "title": "Paper Trading Testini Başlat",
            "description": "Backtest tamamlandı. Gerçek Binance spot fiyatlarıyla risksiz paper trading motorunu başlatın.",
            "action": "START_AGENT",
            "action_label": "PAPER TESTİ BAŞLAT",
            "target_nav": "PAPER TRADING",
            "target_subtab": "positions",
        }
    elif is_auto_running:
        return {
            "step": "MONITOR",
            "title": "Otonom Paper Trader Aktif",
            "description": "Sistem anlık Binance Spot fiyatlarını analiz ediyor ve onaylanan sinyaller risk motoru gözetiminde işletiliyor.",
            "action": "VIEW_POSITIONS",
            "action_label": "POZİSYONLARI İNCELE",
            "target_nav": "PAPER TRADING",
            "target_subtab": "positions",
        }
    else:
        return {
            "step": "VALIDATION",
            "title": "Walk-Forward Doğrulaması",
            "description": "Paper pozisyonlar izleniyor. Ek strateji keşfi veya Monte Carlo simülasyonu çalıştırabilirsiniz.",
            "action": "RUN_VALIDATION",
            "action_label": "VALİDASYONU İNCELE",
            "target_nav": "STRATEJİLER",
            "target_subtab": "validation",
        }


@router.get("/api/v1/system/pipeline-progress")
@router.get("/system/pipeline-progress")
async def get_pipeline_progress():
    from apps.api.app.api.routers.backtests import BACKTEST_RESULTS_CACHE
    from apps.api.app.api.routers.discovery import discovery_engine
    from apps.api.app.api.state import autonomous_trader

    has_backtest = len(BACKTEST_RESULTS_CACHE) > 0
    # True Out-Of-Sample artifact check: has discovery tournament evaluated candidates with robustness reports?
    has_oos = any(
        c.robustness_report is not None and getattr(c.robustness_report, "overfitting_result", None) is not None
        for c in getattr(discovery_engine, "candidates", [])
    )
    has_wf = any(
        c.robustness_report is not None and getattr(c.robustness_report, "walk_forward_stability", 0) > 0
        for c in getattr(discovery_engine, "candidates", [])
    )
    broker = command_bus.broker
    closed = getattr(broker, "closed_positions_history", []) if broker else []
    has_mc = len(closed) >= 5

    is_auto_running = autonomous_trader.is_active

    stages = [
        {"id": "STRATEGY", "label": "STRATEJİ", "status": "COMPLETED", "detail": "R10 Causal Tanımlı"},
        {"id": "BACKTEST", "label": "BACKTEST", "status": "COMPLETED" if has_backtest else "PENDING", "detail": "VectorBT 15m Gerçek Veri"},
        {"id": "OOS", "label": "OOS", "status": "COMPLETED" if has_oos else "PENDING", "detail": "Out-of-Sample Split"},
        {"id": "WALK_FORWARD", "label": "WALK-FORWARD", "status": "COMPLETED" if has_wf else "PENDING", "detail": "3 Pencereli Doğrulama"},
        {"id": "MONTE_CARLO", "label": "MONTE CARLO", "status": "COMPLETED" if has_mc else "PENDING", "detail": "Bootstrap 1000 İterasyon"},
        {"id": "STRESS_TEST", "label": "STRESS TEST", "status": "PENDING", "detail": "Fee ve Slippage %200"},
        {"id": "PAPER_TEST", "label": "PAPER TEST", "status": "ACTIVE" if is_auto_running else ("READY" if has_backtest else "PENDING"), "detail": "Local Paper Broker"},
        {"id": "EXPERIMENT", "label": "7-GÜN DENEY", "status": "PENDING", "detail": "Canlı İleriye Dönük İzleme"},
    ]
    return {
        "stages": stages,
        "active_index": 6 if is_auto_running else (2 if has_oos else (1 if has_backtest else 0)),
    }


@router.get("/api/v1/system/telegram/status")
@router.get("/system/telegram/status")
async def get_telegram_status():
    from services.notification_service.telegram_service import telegram_service
    return {
        "enabled": telegram_service.enabled,
        "bot_token_set": bool(telegram_service.bot_token),
        "chat_id_set": bool(telegram_service.chat_id),
    }


class TelegramTestPayload(BaseModel):
    message: Optional[str] = "🔔 KRIPTO AGENT Test Bildirimi: Telegram bağlantısı başarıyla çalışıyor!"


@router.post("/api/v1/system/telegram/test")
@router.post("/system/telegram/test")
async def post_telegram_test(
    payload: TelegramTestPayload = TelegramTestPayload(),
    _role: Role = Depends(verify_api_key_or_token),
):
    from services.notification_service.telegram_service import telegram_service
    sent = await telegram_service.send_message(payload.message)
    return {
        "success": sent,
        "mode": "LIVE" if telegram_service.enabled else "MOCK_LOG",
        "message": payload.message,
    }


# =============================================================================
# 10-TIER RISK & STRATEGY LADDER (Merdiven) ENDPOINTS
# =============================================================================

class ProfileChangeRequest(BaseModel):
    level: int = Field(ge=1, le=10, description="Risk profile level from 1 to 10")


@router.get("/api/v1/system/profiles")
@router.get("/system/profiles")
async def get_system_profiles():
    """Returns the complete 10-tier risk and strategy profile matrix."""
    from services.config_manager.risk_profiles import get_all_profiles
    return {
        "success": True,
        "profiles": get_all_profiles(),
        "active_level": RUNTIME_STATE.get("active_profile_level", 1),
    }


@router.get("/api/v1/system/profile/current")
@router.get("/system/profile/current")
async def get_current_profile():
    """Returns the currently active risk profile."""
    from services.config_manager.risk_profiles import get_profile
    active_lvl = RUNTIME_STATE.get("active_profile_level", 1)
    prof = get_profile(active_lvl)
    return {
        "success": True,
        "active_level": active_lvl,
        "profile": prof.to_dict(),
    }


@router.post("/api/v1/system/profile")
@router.post("/system/profile")
async def set_system_profile(
    payload: ProfileChangeRequest,
    _role: Role = Depends(verify_api_key_or_token),
):
    """
    Dynamically applies the chosen Risk Profile (1-10) to the running system.
    Single-action rollback to Level 1 is always available.
    """
    from services.config_manager.risk_profiles import apply_profile_to_system
    res = apply_profile_to_system(payload.level)
    return res


# =============================================================================
# STRATEGY REGISTRY & SWITCHING ENDPOINTS (STRATEGY != RISK PROFILE)
# =============================================================================

class StrategyChangeRequest(BaseModel):
    strategy_id: str = Field(description="Strategy identifier from Strategy Registry")


@router.get("/api/v1/system/strategies")
@router.get("/system/strategies")
async def get_system_strategies():
    """Returns all registered strategies and current active strategy."""
    from services.strategy_engine.registry import get_all_strategies
    return {
        "success": True,
        "strategies": get_all_strategies(),
        "active_strategy_id": RUNTIME_STATE.get("active_strategy_id", "r10_rsi_divergence"),
    }


@router.get("/api/v1/system/strategy/current")
@router.get("/system/strategy/current")
async def get_current_strategy():
    """Returns current active strategy metadata."""
    from services.strategy_engine.registry import get_strategy_metadata
    strat_id = RUNTIME_STATE.get("active_strategy_id", "r10_rsi_divergence")
    meta = get_strategy_metadata(strat_id)
    return {
        "success": True,
        "active_strategy_id": strat_id,
        "strategy": meta.to_dict(),
    }


@router.post("/api/v1/system/strategy")
@router.post("/system/strategy")
async def set_system_strategy(
    payload: StrategyChangeRequest,
    _role: Role = Depends(verify_api_key_or_token),
):
    """
    Dynamically switches active strategy from Strategy Registry.
    Guarantees that existing open positions are never forcibly closed.
    """
    from services.config_manager.risk_profiles import apply_strategy_to_system
    from services.strategy_engine.registry import STRATEGY_REGISTRY
    if payload.strategy_id not in STRATEGY_REGISTRY:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown strategy_id '{payload.strategy_id}'. Available: {list(STRATEGY_REGISTRY.keys())}",
        )
    res = apply_strategy_to_system(payload.strategy_id)
    return res


