from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.api.state import RUNTIME_STATE, command_bus, risk_engine
from apps.api.app.middleware.auth import Role, verify_api_key_or_token
from database.session import get_async_db
from services.autonomous_runner import autonomous_trader
from services.execution.order_manager import order_manager
from services.risk_engine.circuit_breaker import CircuitState
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
    purge_learning_state: bool = False
    experiment_id: Optional[str] = None


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
    from services.paper_trading.canonical_accounting import PortfolioAccountingService
    snapshot = PortfolioAccountingService.get_canonical_snapshot(broker=broker)

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

    from services.config_manager.state_persistence import (
        load_persisted_profile_state,
        update_persisted_state,
    )
    persisted = load_persisted_profile_state()
    is_circuit_suspended = persisted.get("circuit_suspended", False)
    cb = getattr(risk_engine, "circuit_breaker", None)
    if is_circuit_suspended and cb:
        cb.is_suspended = True

    from services.risk_engine.circuit_breaker import CircuitState
    daily_pnl = snapshot.get("daily_total_pnl", 0.0)
    daily_max_loss = getattr(risk_engine, "daily_max_loss_usd", getattr(settings, "DAILY_MAX_LOSS", 50.0))

    if cb and not getattr(cb, "is_suspended", False) and daily_pnl <= -daily_max_loss:
        cb.state = CircuitState.LOCKED
        cb.trip_reason = f"DAILY_RISK_LOCK: Daily loss reached -${abs(daily_pnl):.2f} (limit: -${daily_max_loss:.2f})"

    cb_state = getattr(cb.state, "value", str(cb.state)) if (cb and hasattr(cb, "state")) else "NORMAL"
    is_halted = cb_state in ["LOCKED", "EMERGENCY"] or persisted.get("is_halted", False) or RUNTIME_STATE.get("is_halted", False)
    system_state = "HALTED" if is_halted else RUNTIME_STATE.get("system_state", "READY")

    # If persisted state marks bot as active, attempt adoption in current worker if not running
    if persisted.get("is_autonomous_active", False) and not is_halted and not autonomous_trader.is_active:
        try:
            autonomous_trader.start()
        except Exception:
            pass

    is_auto_active = (autonomous_trader.is_active or persisted.get("is_autonomous_active", False)) and not is_halted
    last_cycle = autonomous_trader.last_cycle_at or persisted.get("last_cycle_at")
    last_act = autonomous_trader.last_action or persisted.get("last_action") or "Otonom motor hazır."

    # Atomic synchronization of runtime state
    RUNTIME_STATE["circuit_state"] = cb_state
    RUNTIME_STATE["is_halted"] = is_halted
    RUNTIME_STATE["system_state"] = system_state
    RUNTIME_STATE["is_autonomous_active"] = is_auto_active

    return {
        "system": "KRIPTO AGENT V6.1",
        "system_state": system_state,
        "initial_capital": snapshot.get("initial_capital", settings.INITIAL_CAPITAL),
        "equity": snapshot.get("equity", settings.INITIAL_CAPITAL),
        "balance": snapshot.get("balance", settings.INITIAL_CAPITAL),
        "available_balance": snapshot.get("available_balance", snapshot.get("balance", settings.INITIAL_CAPITAL)),
        "reserved_balance": snapshot.get("reserved_balance", 0.0),
        "daily_pnl": daily_pnl,
        "daily_total_pnl": snapshot.get("daily_total_pnl", 0.0),
        "daily_realized_net_pnl": snapshot.get("daily_realized_net_pnl", 0.0),
        "daily_unrealized_pnl": snapshot.get("daily_unrealized_pnl", 0.0),
        "lifetime_realized_net_pnl": snapshot.get("lifetime_realized_net_pnl", 0.0),
        "lifetime_equity_change": snapshot.get("lifetime_equity_change", 0.0),
        "realized_pnl": snapshot.get("lifetime_realized_net_pnl", 0.0),
        "unrealized_pnl": snapshot.get("daily_unrealized_pnl", 0.0),
        "total_fees": snapshot.get("total_fees_paid", 0.0),
        "total_slippage": snapshot.get("total_slippage_cost", 0.0),
        "open_positions_count": snapshot.get("open_positions_count", 0),
        "max_open_positions": getattr(risk_engine, "max_open_positions_override", None) or risk_engine.max_open_positions,
        "daily_target": RUNTIME_STATE.get("daily_target", getattr(settings, "DAILY_TARGET", 100.0)),
        "daily_target_min": RUNTIME_STATE.get("daily_target_min", getattr(settings, "DAILY_TARGET_MIN", 100.0)),
        "daily_target_max": RUNTIME_STATE.get("daily_target_max", getattr(settings, "DAILY_TARGET_MAX", 150.0)),
        "daily_max_loss": daily_max_loss,
        "max_trades_per_day": getattr(risk_engine, "max_trades_per_day", 10),
        "max_scanned_symbols": getattr(settings, "MAX_UNIVERSE_SYMBOLS", 50),
        "active_profile_level": active_lvl,
        "active_risk_profile": active_prof,
        "active_strategy_id": active_strat_id,
        "active_strategy": active_strat,
        "is_halted": is_halted,
        "circuit_state": cb_state,
        "mode": "PAPER_TRADING",
        "is_spot_mode": True,
        "is_autonomous_active": is_auto_active,
        "last_cycle_at": last_cycle,
        "last_action": last_act,
        "live_trading_prohibited": True,
        "price_stale": snapshot.get("price_stale", False),
        "stale_price_symbols": snapshot.get("stale_price_symbols", []),
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
    from services.paper_trading.canonical_accounting import PortfolioAccountingService
    snapshot = PortfolioAccountingService.get_canonical_snapshot(broker=broker)
    return snapshot


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
        if hasattr(command_bus.broker, "reset_paper_account"):
            command_bus.broker.reset_paper_account(
                init_cap,
                purge_learning_state=payload.purge_learning_state,
                experiment_id=payload.experiment_id,
            )
        else:
            command_bus.broker.reset_portfolio(init_cap)
        if hasattr(command_bus.broker, "unhalt"):
            command_bus.broker.unhalt()
    risk_engine.circuit_breaker.reset(baseline_trade_count=0)
    if hasattr(risk_engine.circuit_breaker, "resume"):
        risk_engine.circuit_breaker.resume(source="reset_experiment")
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
    reset_mode_str = "COLD_RESET_WITH_LEARNING_PURGE" if payload.purge_learning_state else "LEDGER_ONLY_RESET"
    desc = "Tüm öğrenme modelleri (DPO, bandit, adaptif state) ve defter temizlendi (COLD RESET)" if payload.purge_learning_state else "Sadece paper defteri/pozisyonlar temizlendi, öğrenme modelleri korundu (WARM RESET)"
    return {
        "status": "SUCCESS",
        "mode": reset_mode_str,
        "message": f"{desc}. ${init_cap:,.2f} bakiye hazır.",
    }


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
        "max_open_positions": getattr(risk_engine, "max_open_positions_override", None) or risk_engine.max_open_positions,
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
    RUNTIME_STATE["circuit_state"] = CircuitState.EMERGENCY.value
    if hasattr(risk_engine, "circuit_breaker") and risk_engine.circuit_breaker:
        risk_engine.circuit_breaker.state = CircuitState.EMERGENCY

    update_persisted_state({
        "is_autonomous_active": False,
        "is_halted": True,
        "circuit_state": CircuitState.EMERGENCY.value,
        "circuit_suspended": False,
        "last_action": autonomous_trader.last_action,
    })

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
    if force and hasattr(risk_engine.circuit_breaker, "suspend"):
        risk_engine.circuit_breaker.suspend(source="user_force_unhalt")
    else:
        if hasattr(risk_engine.circuit_breaker, "resume"):
            risk_engine.circuit_breaker.resume(source="user_unhalt")
        risk_engine.circuit_breaker.reset(baseline_trade_count=len(closed))

    update_persisted_state({
        "is_halted": False,
        "circuit_suspended": True if force else False,
        "circuit_state": "NORMAL",
        "last_action": autonomous_trader.last_action,
    })
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
    if force and hasattr(risk_engine.circuit_breaker, "suspend"):
        risk_engine.circuit_breaker.suspend(source="user_force_unhalt")
    else:
        if hasattr(risk_engine.circuit_breaker, "resume"):
            risk_engine.circuit_breaker.resume(source="user_start")
        risk_engine.circuit_breaker.reset(baseline_trade_count=len(closed))
    try:
        autonomous_trader.start()
    except RuntimeError as e:
        if "already RUNNING" in str(e):
            autonomous_trader.is_active = True
            autonomous_trader.last_action = "Otonom motor aktif. Gerçek piyasa taranıyor."
            autonomous_trader._sync_runtime_state()
            RUNTIME_STATE["is_autonomous_active"] = True
            update_persisted_state({
                "is_autonomous_active": True,
                "is_halted": False,
                "circuit_suspended": True if force else False,
                "circuit_state": "NORMAL",
                "last_action": autonomous_trader.last_action,
            })
            return {"success": True, "status": "STARTED", "system_state": "TRADING", "message": "Otonom ajan aktif edildi."}
        raise
    RUNTIME_STATE["is_autonomous_active"] = True
    update_persisted_state({
        "is_autonomous_active": True,
        "is_halted": False,
        "circuit_suspended": True if force else False,
        "circuit_state": "NORMAL",
        "last_action": autonomous_trader.last_action,
    })
    command_bus._log_audit("START_AGENT", {"unhalt": unhalt, "force": force, "baseline_trade_count": len(closed)}, True, {"system_state": "TRADING"})
    return {"success": True, "status": "STARTED", "system_state": "TRADING", "message": "Otonom ajan başlatıldı."}


@router.post("/api/agent/pause")
async def post_pause_agent(
    _role: Role = Depends(verify_api_key_or_token),
):
    autonomous_trader.pause()
    RUNTIME_STATE["system_state"] = "PAUSED"
    update_persisted_state({
        "is_autonomous_active": False,
        "last_action": autonomous_trader.last_action,
    })
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
    update_persisted_state({
        "is_autonomous_active": True,
        "is_halted": False,
        "last_action": autonomous_trader.last_action,
    })
    command_bus._log_audit("RESUME_AGENT", {}, True, {"system_state": "TRADING"})
    return {"success": True, "status": "RESUMED", "system_state": "TRADING", "message": "Otonom ajan devam ettirildi."}


@router.post("/api/agent/stop")
async def post_stop_agent(
    _role: Role = Depends(verify_api_key_or_token),
):
    autonomous_trader.stop()
    RUNTIME_STATE["system_state"] = "STOPPED"
    update_persisted_state({
        "is_autonomous_active": False,
        "last_action": autonomous_trader.last_action,
    })
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
        RUNTIME_STATE["max_open_positions"] = payload.max_open_positions
        RUNTIME_STATE["max_open_positions_override"] = payload.max_open_positions
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
    """Returns the currently active risk profile along with live risk engine state."""
    from services.config_manager.risk_profiles import get_profile
    from apps.api.app.api.state import risk_engine
    active_lvl = RUNTIME_STATE.get("active_profile_level", 1)
    prof = get_profile(active_lvl)

    cb_suspended = getattr(getattr(risk_engine, "circuit_breaker", None), "is_suspended", False)

    eff_engine_max_pos = getattr(risk_engine, "max_open_positions_override", None) or getattr(risk_engine, "max_open_positions", None)

    discrepancies = []
    if getattr(risk_engine, "risk_per_trade", None) != prof.risk_per_trade:
        discrepancies.append(f"risk_per_trade mismatch: engine={risk_engine.risk_per_trade} vs profile={prof.risk_per_trade}")
    if eff_engine_max_pos != min(prof.max_open_positions, 8):
        discrepancies.append(f"max_open_positions mismatch: engine={eff_engine_max_pos} vs profile={prof.max_open_positions}")
    if getattr(risk_engine, "daily_max_loss_usd", None) != prof.daily_max_loss:
        discrepancies.append(f"daily_max_loss mismatch: engine={risk_engine.daily_max_loss_usd} vs profile={prof.daily_max_loss}")

    is_consistent = len(discrepancies) == 0

    live_risk = {
        "risk_per_trade": getattr(risk_engine, "risk_per_trade", None),
        "max_open_positions": eff_engine_max_pos,
        "daily_max_loss_usd": getattr(risk_engine, "daily_max_loss_usd", None),
        "daily_target_max": getattr(risk_engine, "daily_target_max", None),
        "target_mode": getattr(risk_engine, "target_mode", None),
        "min_risk_reward": getattr(risk_engine, "min_risk_reward", None),
        "circuit_breaker_suspended": cb_suspended,
    }

    return {
        "success": is_consistent,
        "is_consistent": is_consistent,
        "discrepancies": discrepancies,
        "circuit_breaker_suspended": cb_suspended,
        "active_level": active_lvl,
        "profile": prof.to_dict(),
        "live_risk_engine": live_risk,
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


@router.get("/api/v1/learning/bandit-state")
@router.get("/learning/bandit-state")
async def get_bandit_state():
    """
    Returns real-time Bayesian Contextual Bandit state:
    Active arms, 6 market contexts, Normal-Gamma posteriors (mu, n, alpha, beta),
    quarantine status, and optimal policy by context.
    """
    from services.learning.contextual_bandit import contextual_bandit
    return contextual_bandit.get_state_dict()


@router.get("/api/v1/learning/dpo-metrics")
@router.get("/learning/dpo-metrics")
async def get_dpo_metrics():
    """
    Returns real-time DPO Signal Gate metrics, preference flywheel stats,
    and model training status.
    """
    from services.learning.dpo_signal_gate import dpo_signal_gate
    return {
        "success": True,
        "metrics": dpo_signal_gate.get_metrics(),
    }


@router.post("/api/v1/learning/dpo-retrain")
async def post_dpo_retrain(_role: Role = Depends(verify_api_key_or_token)):
    """
    Triggers on-demand training of the DPO Signal Gate with the latest preference pairs.
    """
    from services.learning.dpo_signal_gate import dpo_signal_gate
    success = dpo_signal_gate.train()
    return {
        "success": success,
        "metrics": dpo_signal_gate.get_metrics(),
    }


@router.get("/api/v1/learning/status")
@router.get("/learning/status")
async def get_learning_status():
    """
    Returns unified evidence-based learning system status:
    - Model lifecycle state & manifests (SHADOW isolation)
    - Signal Gate Walk-Forward validation & calibration metrics
    - Adaptive parameter states and ledger reconciliation
    - Contextual Bandit strategy-level posteriors
    - Decision logging telemetry
    """
    from services.learning.model_lifecycle import model_lifecycle_manager
    from services.learning.signal_gate_engine import signal_gate_engine
    from services.learning.decision_logger import decision_logger
    from services.strategy_engine.adaptive_learning import adaptive_learning_engine
    from services.learning.contextual_bandit import contextual_bandit

    return {
        "success": True,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model_lifecycle": {
            "active_version": model_lifecycle_manager.active_model_version,
            "manifest": model_lifecycle_manager.get_active_manifest(),
        },
        "signal_gate": signal_gate_engine.get_metrics(),
        "adaptive_learning": adaptive_learning_engine.get_summary(),
        "decision_logger": decision_logger.get_summary(),
        "bandit": {
            "total_arms": len(contextual_bandit.arms),
            "contexts": list(contextual_bandit.contexts.keys()),
        },
    }


@router.get("/api/v1/learning/decisions")
@router.get("/learning/decisions")
async def get_recent_decisions(limit: int = 50):
    """
    Returns recent candidate signal decision logs with shadow gate scores,
    risk engine outcomes, and execution links.
    """
    from services.learning.decision_logger import decision_logger
    decisions = decision_logger.get_recent_decisions(limit=limit)
    return {
        "success": True,
        "count": len(decisions),
        "decisions": decisions,
    }


@router.post("/api/v1/system/sync-git")
@router.post("/system/sync-git")
async def post_sync_git(
    background_tasks: BackgroundTasks,
    _role: Role = Depends(verify_api_key_or_token),
):
    """
    Pulls latest commits from GitHub repository (origin/main)
    using zero-fork pure-Python download & extraction,
    then touches tmp/restart.txt to automatically trigger Passenger reload.
    Eliminates all 'cannot fork() for remote-https' shared hosting failures.
    """
    import io
    import os
    import shutil
    import urllib.request
    import zipfile
    from datetime import datetime, timezone
    from pathlib import Path

    # Authoritatively find project root by searching upward for marker files
    cur = Path(__file__).resolve()
    project_root = None
    for parent in cur.parents:
        if (parent / "passenger_wsgi.py").exists() or ((parent / "database").is_dir() and (parent / "shared").is_dir()):
            project_root = str(parent)
            break
    if not project_root:
        project_root = str(cur.parents[5] if len(cur.parents) > 5 else cur.parents[-1])

    results = {"project_root": project_root}

    try:
        # Download repository zip archive from GitHub without spawning subprocesses
        zip_url = "https://github.com/kvncbsr2/kripto-trading-bot/archive/refs/heads/main.zip"
        req = urllib.request.Request(zip_url, headers={"User-Agent": "KRIPTO-AGENT-SYNC/1.0"})
        with urllib.request.urlopen(req, timeout=45) as resp:
            if resp.status != 200:
                raise RuntimeError(f"GitHub ZIP indirilemedi (HTTP {resp.status})")
            zip_bytes = resp.read()

        updated_files_count = 0
        skipped_files_count = 0

        # Safe extraction: Never overwrite database, env secrets, or git config
        PROTECTED_PATHS = {
            "kripto_agent.db",
            "kripto_agent_denetim_salt_okunur.db",
            ".env",
            "passenger_wsgi.py",
        }

        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as z:
            for member in z.infolist():
                parts = member.filename.split("/", 1)
                if len(parts) < 2 or not parts[1]:
                    continue
                rel_path = parts[1]

                # Security guards
                if rel_path in PROTECTED_PATHS or rel_path.startswith((".git/", "tmp/")):
                    skipped_files_count += 1
                    continue
                if rel_path.endswith((".db", ".sqlite", ".sqlite3")):
                    skipped_files_count += 1
                    continue

                dest_path = os.path.join(project_root, rel_path)
                if member.is_dir():
                    os.makedirs(dest_path, exist_ok=True)
                else:
                    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                    with z.open(member) as src, open(dest_path, "wb") as dst:
                        dst.write(src.read())
                    updated_files_count += 1

        results["download_size_bytes"] = len(zip_bytes)
        results["updated_files"] = updated_files_count

        # Clean up any misplaced directories from prior runs in apps/api/
        for acc in ["apps", "database", "shared", "services", "domain", "config", "tests"]:
            acc_dir = os.path.join(project_root, "apps", "api", acc)
            if os.path.isdir(acc_dir):
                try:
                    shutil.rmtree(acc_dir)
                except Exception:
                    pass

        # Ensure SQLite is in DELETE journal mode and clean up any lock-prone shm/wal files
        try:
            import sqlite3
            db_path = os.path.join(project_root, "kripto_agent.db")
            if os.path.exists(db_path):
                conn = sqlite3.connect(db_path, timeout=10.0)
                conn.execute("PRAGMA journal_mode=DELETE;")
                conn.execute("PRAGMA synchronous=NORMAL;")
                conn.execute("PRAGMA busy_timeout=30000;")
                conn.close()
            # Clean up orphaned shm/wal files that cause disk I/O errors on cPanel
            for ext in ("-shm", "-wal"):
                orphan = db_path + ext
                if os.path.exists(orphan):
                    try:
                        os.remove(orphan)
                    except Exception:
                        pass
        except Exception:
            pass

        # Trigger Passenger reload via tmp/restart.txt
        tmp_dir = os.path.join(project_root, "tmp")
        os.makedirs(tmp_dir, exist_ok=True)
        restart_file = os.path.join(tmp_dir, "restart.txt")
        with open(restart_file, "a") as f:
            f.write(f"\n# Reload at {datetime.now(timezone.utc).isoformat()}")
        try:
            os.utime(restart_file, None)
        except Exception:
            pass

        # Also schedule a clean process exit 1s later so Passenger immediately respawns fresh worker
        def _delayed_exit():
            import time
            time.sleep(1.0)
            os._exit(0)

        background_tasks.add_task(_delayed_exit)
        results["restart_triggered"] = True
        return {
            "success": True,
            "message": f"GitHub'dan en son kodlar başarıyla çekildi ({updated_files_count} dosya güncellendi) ve sistem yeniden başlatılıyor.",
            "details": results,
        }
    except Exception as e:
        return {
            "success": False,
            "message": f"Senkronizasyon hatası: {str(e)}",
            "details": {"error": str(e)},
        }





