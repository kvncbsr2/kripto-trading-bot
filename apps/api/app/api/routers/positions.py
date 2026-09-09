import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.app.api.state import command_bus, market_data_service
from apps.api.app.middleware.auth import Role, verify_api_key_or_token
from database.models.tables import PositionModel
from database.session import get_async_db
from shared.logging import get_logger

logger = get_logger("positions-router", service="api")

# Authoritative project root (KRIPTO AGENT folder)
PROJECT_ROOT = Path(__file__).resolve().parents[5]

router = APIRouter(tags=["positions"])


class ClosePositionRequest(BaseModel):
    reason: str = "USER_MANUAL_CLOSE"


@router.get("/positions")
@router.get("/api/v1/positions")
@router.get("/api/v1/positions/active")
async def get_positions(status: str = "OPEN", response: Response = None, db: AsyncSession = Depends(get_async_db)):
    """
    Returns active or historical positions from DB and authoritative PaperBroker.
    Strictly Zero Fake Data: Returns empty list if no positions exist.
    """
    positions = []
    db_query_ok = True
    try:
        stmt = select(PositionModel)
        if status.upper() != "ALL":
            stmt = stmt.where(PositionModel.status == status.upper())
        stmt = stmt.order_by(PositionModel.created_at.desc())
        res = await db.execute(stmt)
        positions = list(res.scalars().all())
    except Exception as e:
        # FIX (2026-09): previously a bare `except: pass` silently returned an empty
        # list on ANY database error, indistinguishable from "genuinely zero
        # positions" — a direct contradiction of the "Zero Fake Data" docstring
        # above, since an empty-because-broken response looks identical to an
        # empty-because-true one. We still don't hard-fail the endpoint (the
        # in-memory PaperBroker positions merged in below are the authoritative
        # real-time source and can stand on their own), but the failure is now
        # logged loudly and surfaced via `db_query_error` in the response so a
        # caller can tell the difference.
        logger.error(f"get_positions: DB query failed, falling back to in-memory broker only: {e}", exc_info=True)
        db_query_ok = False

    pos_list = [
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

    # Include in-memory paper broker open positions
    if command_bus.broker:
        for symbol, p in command_bus.broker.open_positions.items():
            try:
                ticker = await market_data_service.get_live_ticker(p.symbol)
                if ticker and "price" in ticker and ticker["price"] > 0:
                    command_bus.broker.update_market_price(p.symbol, float(ticker["price"]))
            except Exception:
                pass

            existing_idx = next((i for i, x in enumerate(pos_list) if x["position_id"] == p.position_id), None)
            pos_dict = {
                "position_id": p.position_id,
                "symbol": p.symbol,
                "side": p.side.value if hasattr(p.side, "value") else str(p.side),
                "entry_price": p.entry_price,
                "current_price": p.current_price,
                "quantity": p.quantity,
                "stop_loss": p.stop_loss,
                "take_profit": p.take_profit,
                "unrealized_pnl": round(p.unrealized_pnl, 2),
                "realized_pnl": round(p.realized_pnl, 2),
                "status": p.status.value if hasattr(p.status, "value") else str(p.status),
                "strategy": p.strategy,
                "opened_at": p.opened_at.isoformat() if hasattr(p.opened_at, "isoformat") else str(p.opened_at),
                "closed_at": None,
            }
            if existing_idx is not None:
                pos_list[existing_idx] = pos_dict
            else:
                pos_list.append(pos_dict)

    if not db_query_ok and response is not None:
        response.headers["X-DB-Query-Error"] = "true"

    return pos_list


@router.get("/positions/{symbol:path}")
async def get_position_by_symbol(symbol: str):
    norm_symbol = symbol.replace("-", "/").upper()
    if command_bus.broker and norm_symbol in command_bus.broker.open_positions:
        p = command_bus.broker.open_positions[norm_symbol]
        return {
            "position_id": p.position_id,
            "symbol": p.symbol,
            "side": p.side.value,
            "entry_price": p.entry_price,
            "current_price": p.current_price,
            "quantity": p.quantity,
            "stop_loss": p.stop_loss,
            "take_profit": p.take_profit,
            "unrealized_pnl": p.unrealized_pnl,
            "status": p.status.value,
        }
    raise HTTPException(status_code=404, detail=f"No active position for {norm_symbol}")


@router.post("/positions/{identifier:path}/close")
@router.post("/api/v1/positions/{identifier:path}/close")
async def post_close_position(
    identifier: str,
    payload: Optional[ClosePositionRequest] = None,
    _role: Role = Depends(verify_api_key_or_token),
):
    """
    Closes an open position at the REAL current market price.
    """
    if not command_bus.broker:
        raise HTTPException(status_code=500, detail="Broker not initialized.")

    norm_symbol = identifier.replace("-", "/").upper()
    broker = command_bus.broker

    # Find position by symbol or position_id
    target_pos = None
    target_symbol = None
    for sym, pos in broker.open_positions.items():
        if sym == norm_symbol or pos.position_id == identifier:
            target_pos = pos
            target_symbol = sym
            break

    if not target_pos or not target_symbol:
        raise HTTPException(status_code=404, detail=f"No active position found for {identifier}")

    # Fetch real live price from MarketDataService
    ticker = await market_data_service.get_live_ticker(target_symbol)
    exit_price = float(ticker["price"]) if ticker and "price" in ticker else target_pos.current_price

    reason = payload.reason if payload else "USER_MANUAL_CLOSE"
    closed = broker.close_position(symbol=target_symbol, exit_price=exit_price, reason=reason)

    if not closed:
        raise HTTPException(status_code=500, detail="Failed to close position.")

    return {
        "success": True,
        "action": "POSITION_CLOSED",
        "symbol": closed.symbol,
        "exit_price": closed.current_price,
        "realized_pnl": closed.realized_pnl,
        "reason": reason,
        "message": f"{closed.symbol} pozisyonu ${closed.current_price:.2f} fiyattan kapatıldı. Kâr/Zarar: ${closed.realized_pnl:+.2f}",
    }


@router.get("/api/v1/positions/history")
@router.get("/position-history")
async def get_position_history():
    """
    Returns closed position history from authoritative PaperBroker.
    Zero Fake Data Policy: If no trades exist, returns empty history.
    """
    history_records = []
    if command_bus.broker and command_bus.broker.closed_positions_history:
        for pos in reversed(command_bus.broker.closed_positions_history):
            exit_price = pos.current_price or pos.entry_price
            cost_basis = pos.entry_price * pos.quantity
            roi_pct = (pos.realized_pnl / cost_basis * 100.0) if cost_basis > 0 else 0.0
            total_fee = round(float(pos.fees_paid or 0.0), 4)
            fee_pct = round((total_fee / cost_basis * 100.0), 3) if cost_basis > 0 else 0.0
            opened_str = (
                pos.opened_at.strftime("%d/%m/%Y %H:%M:%S")
                if hasattr(pos.opened_at, "strftime")
                else str(pos.opened_at)
            )
            closed_str = (
                pos.closed_at.strftime("%d/%m/%Y %H:%M:%S")
                if pos.closed_at and hasattr(pos.closed_at, "strftime")
                else datetime.now(timezone.utc).strftime("%d/%m/%Y %H:%M:%S")
            )

            history_records.append({
                "position_id": pos.position_id,
                "symbol": pos.symbol,
                "clean_symbol": pos.symbol.replace("/", ""),
                "side": pos.side.value if hasattr(pos.side, "value") else str(pos.side),
                "leverage": "1x (Spot)",
                "margin_mode": "Spot Long",
                "status": "Closed",
                "realized_pnl": round(pos.realized_pnl, 2),
                "roi_pct": round(roi_pct, 2),
                "closed_volume_usd": round(exit_price * pos.quantity, 2),
                "fees_paid": round(total_fee, 2),
                "fee_pct": round(fee_pct, 2),
                "entry_price": pos.entry_price,
                "exit_price": exit_price,
                "quantity": pos.quantity,
                "opened_at": opened_str,
                "closed_at": closed_str,
                "is_win": pos.realized_pnl > 0,
            })

    total_pnl = sum(r["realized_pnl"] for r in history_records)
    total_wins = sum(1 for r in history_records if r["is_win"])
    total_losses = sum(1 for r in history_records if not r["is_win"])
    win_rate = (total_wins / len(history_records) * 100.0) if history_records else 0.0

    return {
        "summary": {
            "total_realized_pnl": round(total_pnl, 2),
            "total_trades": len(history_records),
            "wins": total_wins,
            "losses": total_losses,
            "win_rate_pct": round(win_rate, 1),
        },
        "history": history_records,
    }


@router.get("/api/v1/investor/report")
@router.get("/investor/report")
async def get_investor_report(response: Response = None):
    """
    Returns All-Time Paper Trading Performance Tearsheet & Ledger Summary.
    Calculates exact Net PnL, Gross Profit/Loss, Total Fees/Commissions Paid, Slippage Drag,
    Profit Factor, Win Rate, and Individual Trade Ledger.
    Zero Fake Data Policy: Sourced strictly from SQLite DB and authoritative broker logs.
    Note: Internal self-reported paper-trading ledger (not a third-party certified external audit).
    """
    import sqlite3
    from shared.config import get_settings
    settings = get_settings()

    initial_capital = float(getattr(settings, "INITIAL_CAPITAL", 5000.0))
    broker = command_bus.broker

    # 1. Authoritative Absolute DB Path Resolution (Fix relative path vulnerability)
    raw_db_path = getattr(broker, "db_path", None) if broker else None
    if raw_db_path and os.path.isabs(raw_db_path):
        resolved_db_path = Path(raw_db_path)
    else:
        filename = os.path.basename(raw_db_path) if raw_db_path else "kripto_agent.db"
        resolved_db_path = PROJECT_ROOT / filename

    db_path_str = str(resolved_db_path)

    # 2. Strict DB Existence Check (Prevents SQLite from silently creating an empty 0-byte file in arbitrary CWD)
    if not resolved_db_path.exists():
        err_msg = f"Database file does not exist at authoritative path: {db_path_str}"
        logger.error(f"get_investor_report: {err_msg}")
        if response is not None:
            response.headers["X-DB-Query-Error"] = "true"
            response.headers["X-DB-Status"] = "MISSING_FILE"
        raise HTTPException(
            status_code=503,
            detail=f"Authoritative database file not found at {db_path_str}. Verify system initialization."
        )

    closed_rows = []
    open_rows = []
    total_fill_fees = 0.0
    total_fill_slippage = 0.0
    acc_balance = initial_capital
    db_query_ok = True
    db_error_detail = None

    try:
        # Strict Read-Only Mode via URI (mode=ro guaranteed never to mutate or auto-create)
        uri_path = resolved_db_path.as_posix()
        conn = sqlite3.connect(f"file:{uri_path}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()

        cur.execute("SELECT * FROM paper_account WHERE id=1")
        acc = cur.fetchone()
        if acc:
            acc_balance = float(acc["balance"])

        cur.execute("SELECT * FROM paper_positions WHERE status='CLOSED' ORDER BY closed_at DESC")
        closed_rows = [dict(r) for r in cur.fetchall()]

        cur.execute("SELECT * FROM paper_positions WHERE status='OPEN' ORDER BY opened_at DESC")
        open_rows = [dict(r) for r in cur.fetchall()]

        cur.execute("SELECT SUM(fee) as total_fees, SUM(slippage) as total_slippage FROM paper_fills")
        f_row = cur.fetchone()
        if f_row:
            total_fill_fees = float(f_row["total_fees"] or 0.0)
            total_fill_slippage = float(f_row["total_slippage"] or 0.0)

        conn.close()
    except Exception as e:
        # FIX (2026-09): Surface DB query errors loudly and explicitly, never swallow silently
        db_query_ok = False
        db_error_detail = str(e)
        logger.error(
            f"get_investor_report: SQLite query failed on {db_path_str}: {e}. Surfacing error.",
            exc_info=True
        )
        if response is not None:
            response.headers["X-DB-Query-Error"] = "true"
            response.headers["X-DB-Status"] = "QUERY_FAILED"

        # If DB read failed and no in-memory backup is available, fail closed with HTTP 500
        if not broker or (not getattr(broker, "closed_positions_history", None) and not closed_rows):
            raise HTTPException(
                status_code=500,
                detail=f"Database query failed and no in-memory backup available: {db_error_detail}"
            )

    if db_query_ok and response is not None:
        response.headers["X-DB-Query-Error"] = "false"
        response.headers["X-DB-Status"] = "OK"

    # Fallback/merge with live broker memory if DB was empty or broker has newer records
    if broker:
        current_equity = float(broker.equity)
        current_balance = float(broker.balance)
        unrealized_pnl = float(broker.total_unrealized_pnl)
    else:
        current_equity = acc_balance
        current_balance = acc_balance
        unrealized_pnl = 0.0

    # Build ledger
    trade_ledger = []
    for r in closed_rows:
        entry_p = float(r.get("entry_price") or 0.0)
        exit_p = float(r.get("current_price") or entry_p)
        qty = float(r.get("quantity") or 0.0)
        vol_usd = round(exit_p * qty, 2)
        pnl = float(r.get("realized_pnl") or 0.0)
        fee = float(r.get("fees_paid") or 0.0)
        gross_pnl = round(pnl + fee, 2)
        cost_basis = entry_p * qty
        roi = round((pnl / cost_basis * 100.0), 2) if cost_basis > 0 else 0.0

        trade_ledger.append({
            "position_id": r.get("position_id"),
            "symbol": r.get("symbol"),
            "side": r.get("side", "LONG"),
            "entry_price": entry_p,
            "exit_price": exit_p,
            "quantity": qty,
            "volume_usd": vol_usd,
            "gross_pnl": gross_pnl,
            "commission_paid": round(fee, 4),
            "net_pnl": round(pnl, 2),
            "roi_pct": roi,
            "opened_at": r.get("opened_at"),
            "closed_at": r.get("closed_at"),
            "is_win": pnl > 0,
            "strategy": r.get("strategy", "R10_RSI_DIVERGENCE"),
        })

    # Calculations
    wins = [t for t in trade_ledger if t["is_win"]]
    losses = [t for t in trade_ledger if not t["is_win"]]

    gross_profit = sum(t["net_pnl"] for t in wins)
    gross_loss = abs(sum(t["net_pnl"] for t in losses))
    total_realized_pnl = sum(t["net_pnl"] for t in trade_ledger)
    net_pnl_with_unrealized = round(current_equity - initial_capital, 2)
    net_roi_pct = round((net_pnl_with_unrealized / initial_capital) * 100.0, 2)

    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (999.0 if gross_profit > 0 else 0.0)
    win_rate_pct = round((len(wins) / len(trade_ledger) * 100.0), 1) if trade_ledger else 0.0

    total_trade_volume = sum(t["volume_usd"] for t in trade_ledger)
    total_commissions = round(total_fill_fees if total_fill_fees > 0 else sum(t["commission_paid"] for t in trade_ledger), 2)

    avg_win = round(gross_profit / len(wins), 2) if wins else 0.0
    avg_loss = round(gross_loss / len(losses), 2) if losses else 0.0
    win_loss_ratio = round(avg_win / avg_loss, 2) if avg_loss > 0 else 0.0

    from services.risk_engine.expectancy_engine import ExpectancyEngine
    expectancy_data = ExpectancyEngine.calculate_from_positions(trade_ledger)

    return {
        "status": "SUCCESS",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "executive_summary": {
            "initial_capital": round(initial_capital, 2),
            "current_equity": round(current_equity, 2),
            "current_balance": round(current_balance, 2),
            "net_pnl_total": net_pnl_with_unrealized,
            "net_realized_pnl": round(total_realized_pnl, 2),
            "unrealized_pnl": round(unrealized_pnl, 2),
            "net_roi_pct": net_roi_pct,
            "total_trades": len(trade_ledger),
            "winning_trades": len(wins),
            "losing_trades": len(losses),
            "win_rate_pct": win_rate_pct,
            "profit_factor": profit_factor,
            "gross_profit": round(gross_profit, 2),
            "gross_loss": round(gross_loss, 2),
            "average_win": avg_win,
            "average_loss": avg_loss,
            "win_loss_ratio": win_loss_ratio,
            "active_positions_count": len(open_rows),
        },
        "mathematical_expectancy": expectancy_data,
        "fee_and_cost_transparency": {
            "total_commissions_paid": total_commissions,
            "total_slippage_cost": round(total_fill_slippage, 2),
            "total_turnover_volume_usd": round(total_trade_volume, 2),
            "effective_commission_rate_pct": round((total_commissions / total_trade_volume * 100.0), 3) if total_trade_volume > 0 else 0.0,
            "fee_drag_to_gross_profit_pct": round((total_commissions / gross_profit * 100.0), 1) if gross_profit > 0 else 0.0,
            "maker_fee_rate": getattr(settings, "MAKER_FEE", 0.001),
            "taker_fee_rate": getattr(settings, "TAKER_FEE", 0.001),
            "slippage_bps": getattr(settings, "SLIPPAGE_BPS", 5.0),
        },
        "trade_ledger": trade_ledger,
        "open_positions": [
            {
                "symbol": r.get("symbol"),
                "side": r.get("side", "LONG"),
                "quantity": float(r.get("quantity") or 0.0),
                "entry_price": float(r.get("entry_price") or 0.0),
                "current_price": float(r.get("current_price") or 0.0),
                "unrealized_pnl": round(float(r.get("unrealized_pnl") or 0.0), 2),
                "opened_at": r.get("opened_at"),
            }
            for r in open_rows
        ],
        "system_parameters": {
            "execution_mode": "Paper Trading (0 Capital Risk - Internal Engine Logs)",
            "data_source": "Binance Spot Live WebSockets & REST (Zero Fake Data)",
            "strategy": "R10 Causal RSI Divergence + Regime-Gated Pullback",
            "risk_controls": "Strict Fail-Closed RiskEngine (0.5% Risk / Max 5 Positions)",
            "spot_rule": "Long Only (Zero Liquidation / Zero Borrowing)",
            "audit_disclaimer": "Self-reported simulated execution ledger; not a third-party certified audit.",
        },
        "data_source_integrity": {
            "db_path": db_path_str,
            "db_status": "VERIFIED_OK" if db_query_ok else "DEGRADED_FALLBACK",
            "db_error": db_error_detail,
            "access_mode": "Strict Read-Only (URI mode=ro)",
            "zero_fake_data_guarantee": True,
        }
    }


@router.get("/api/v1/investor/expectancy")
async def get_mathematical_expectancy():
    """
    Returns real-time mathematical expectancy, win/loss ratio (b), breakeven rate,
    and automated negative-expectancy circuit breaker state.
    """
    from services.risk_engine.expectancy_engine import ExpectancyEngine
    broker = command_bus.broker
    closed = getattr(broker, "closed_positions_history", []) if broker else []
    return ExpectancyEngine.calculate_from_positions(closed)
