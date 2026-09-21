import math
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from shared.config import get_settings
from shared.logging import get_logger

logger = get_logger("canonical-accounting", service="paper_trading")
settings = get_settings()

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class PortfolioAccountingService:
    """
    Single authoritative canonical accounting and reporting engine for KRIPTO AGENT.
    Eliminates diverging PnL and metric formulas across endpoints.
    """

    @classmethod
    def get_canonical_snapshot(cls, broker: Any = None, db_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Calculates canonical portfolio state directly from SQLite and live broker.
        Guarantees mathematical equality between ledger and all reporting endpoints.
        """
        initial_capital = float(getattr(settings, "INITIAL_CAPITAL", 10000.0))
        resolved_db = db_path or (getattr(broker, "db_path", None) if broker else None)
        if not resolved_db or not os.path.exists(resolved_db):
            resolved_db = str(PROJECT_ROOT / "kripto_agent.db")

        conn = None
        closed_rows = []
        open_rows = []
        fills_rows = []
        acc_row = None

        if os.path.exists(resolved_db):
            try:
                uri = Path(resolved_db).as_posix()
                conn = sqlite3.connect(f"file:{uri}?mode=ro", uri=True, timeout=10.0)
                conn.row_factory = sqlite3.Row
                cur = conn.cursor()

                cur.execute("SELECT * FROM paper_account WHERE id=1")
                acc_fetch = cur.fetchone()
                acc_row = dict(acc_fetch) if acc_fetch else None

                cur.execute("SELECT * FROM paper_positions WHERE status='CLOSED' ORDER BY closed_at ASC")
                closed_rows = [dict(r) for r in cur.fetchall()]

                cur.execute("SELECT * FROM paper_positions WHERE status='OPEN' ORDER BY opened_at ASC")
                open_rows = [dict(r) for r in cur.fetchall()]

                cur.execute("SELECT * FROM paper_fills ORDER BY timestamp ASC")
                fills_rows = [dict(r) for r in cur.fetchall()]
            except Exception as e:
                logger.error(f"CanonicalAccounting: SQLite read error on {resolved_db}: {e}")
            finally:
                if conn:
                    conn.close()

        # Balance resolution
        if broker and hasattr(broker, "balance") and broker.balance > 0:
            balance = round(float(broker.balance), 2)
            avail_balance = round(float(broker.available_balance), 2)
            res_balance = round(float(broker.reserved_balance), 2)
            initial_balance = round(float(getattr(broker, "initial_balance", initial_capital)), 2)
        elif acc_row:
            balance = round(float(acc_row["balance"]), 2)
            avail_balance = round(float(acc_row["available_balance"]), 2)
            res_balance = round(float(acc_row["reserved_balance"]), 2)
            initial_balance = round(float(acc_row["initial_balance"]), 2)
        else:
            balance = initial_capital
            avail_balance = initial_capital
            res_balance = 0.0
            initial_balance = initial_capital

        # Open positions & price staleness
        open_positions_list = []
        open_unrealized_pnl = 0.0
        stale_symbols = []

        broker_positions = broker.open_positions if (broker and hasattr(broker, "open_positions")) else {}

        if broker_positions:
            for sym, p in broker_positions.items():
                p_status = p.status.value if hasattr(p.status, "value") else str(p.status)
                if p_status != "OPEN":
                    continue
                failures = getattr(p, "price_fetch_failures", 0)
                is_stale = bool(getattr(p, "price_stale", False) or failures >= 5)
                if is_stale:
                    stale_symbols.append(sym)
                u_pnl = round(float(p.unrealized_pnl), 2)
                open_unrealized_pnl += u_pnl
                last_ts = getattr(p, "last_price_update_at", None)
                open_positions_list.append({
                    "position_id": p.position_id,
                    "symbol": p.symbol,
                    "side": p.side.value if hasattr(p.side, "value") else str(p.side),
                    "entry_price": round(float(p.entry_price), 6),
                    "current_price": round(float(p.current_price), 6),
                    "quantity": round(float(p.quantity), 6),
                    "stop_loss": round(float(p.stop_loss), 6) if p.stop_loss else None,
                    "take_profit": round(float(p.take_profit), 6) if p.take_profit else None,
                    "unrealized_pnl": u_pnl,
                    "fees_paid": round(float(p.fees_paid or 0.0), 4),
                    "strategy": p.strategy or "MANUAL",
                    "price_stale": is_stale,
                    "price_fetch_failures": failures,
                    "last_price_update_at": last_ts.isoformat() if hasattr(last_ts, "isoformat") else (str(last_ts) if last_ts else None),
                    "opened_at": p.opened_at.isoformat() if hasattr(p.opened_at, "isoformat") else str(p.opened_at),
                })
        else:
            for r in open_rows:
                sym = r["symbol"]
                failures = int(r.get("price_fetch_failures") or 0)
                is_stale = bool(r.get("price_stale") or failures >= 5)
                if is_stale:
                    stale_symbols.append(sym)
                u_pnl = round(float(r.get("unrealized_pnl") or 0.0), 2)
                open_unrealized_pnl += u_pnl
                open_positions_list.append({
                    "position_id": r["position_id"],
                    "symbol": sym,
                    "side": r["side"],
                    "entry_price": round(float(r["entry_price"]), 6),
                    "current_price": round(float(r["current_price"]), 6),
                    "quantity": round(float(r["quantity"]), 6),
                    "stop_loss": round(float(r["stop_loss"]), 6) if r.get("stop_loss") else None,
                    "take_profit": round(float(r["take_profit"]), 6) if r.get("take_profit") else None,
                    "unrealized_pnl": u_pnl,
                    "fees_paid": round(float(r.get("fees_paid") or 0.0), 4),
                    "strategy": r.get("strategy") or "MANUAL",
                    "price_stale": is_stale,
                    "price_fetch_failures": failures,
                    "last_price_update_at": r.get("last_price_update_at"),
                    "opened_at": r.get("opened_at"),
                })

        open_unrealized_pnl = round(open_unrealized_pnl, 2)
        equity = round(balance + open_unrealized_pnl, 2)

        # Fills accounting
        total_fees = round(sum(float(f.get("fee") or 0.0) for f in fills_rows), 4)
        total_slippage = round(sum(float(f.get("slippage") or 0.0) for f in fills_rows), 4)

        now_utc = datetime.now(timezone.utc)
        today_date = now_utc.date()

        if broker and hasattr(broker, "day_start_equity") and getattr(broker, "day_start_date", None) == today_date:
            day_start_eq = round(float(broker.day_start_equity), 2)
        elif acc_row and acc_row.get("day_start_date") == str(today_date):
            day_start_eq = round(float(acc_row.get("day_start_equity") or balance), 2)
        else:
            day_start_eq = balance

        total_closed_pnl = sum(float(r.get("realized_pnl") or 0.0) for r in closed_rows)
        total_partial_pnl = sum(float(r.get("partial_realized_pnl") or 0.0) for r in open_rows if r.get("partial_tp_hit"))
        lifetime_realized_pnl = round(total_closed_pnl + total_partial_pnl, 2)
        lifetime_equity_change = round(equity - initial_balance, 2)

        daily_closed_pnl = 0.0
        for r in closed_rows:
            c_at = r.get("closed_at")
            if c_at:
                try:
                    c_dt = datetime.fromisoformat(c_at)
                    if c_dt.date() == today_date:
                        daily_closed_pnl += float(r.get("realized_pnl") or 0.0)
                except Exception:
                    pass

        daily_partial_pnl = 0.0
        for r in open_rows:
            if r.get("partial_tp_hit") and r.get("partial_realized_at"):
                try:
                    p_dt = datetime.fromisoformat(r["partial_realized_at"])
                    if p_dt.date() == today_date:
                        daily_partial_pnl += float(r.get("partial_realized_pnl") or 0.0)
                except Exception:
                    pass

        daily_realized_net_pnl = round(daily_closed_pnl + daily_partial_pnl, 2)
        daily_unrealized_pnl = open_unrealized_pnl
        daily_total_pnl = round(daily_realized_net_pnl + daily_unrealized_pnl, 2)

        peak_equity = max(initial_balance, equity)
        max_dd = (peak_equity - equity) / peak_equity if peak_equity > equity else 0.0

        return {
            "initial_capital": initial_balance,
            "balance": balance,
            "available_balance": avail_balance,
            "reserved_balance": res_balance,
            "equity": equity,
            "day_start_equity": day_start_eq,
            "daily_pnl": daily_total_pnl,
            "daily_total_pnl": daily_total_pnl,
            "daily_realized_net_pnl": daily_realized_net_pnl,
            "daily_unrealized_pnl": daily_unrealized_pnl,
            "lifetime_realized_net_pnl": lifetime_realized_pnl,
            "lifetime_equity_change": lifetime_equity_change,
            "realized_pnl": lifetime_realized_pnl,
            "unrealized_pnl": daily_unrealized_pnl,
            "total_fees_paid": total_fees,
            "total_slippage_cost": total_slippage,
            "max_drawdown": round(max_dd, 4),
            "open_positions_count": len(open_positions_list),
            "open_positions": open_positions_list,
            "closed_positions_count": len(closed_rows),
            "price_stale": len(stale_symbols) > 0,
            "stale_price_symbols": list(set(stale_symbols)),
            "currency": "USDT",
            "mode": "PAPER_TRADING",
            "live_market_data": True,
            "live_money_execution": False,
            "snapshot_time_utc": now_utc.isoformat(),
        }

    @classmethod
    def get_strategy_performance_attribution(
        cls, db_path: Optional[str] = None, initial_capital: float = 10000.0
    ) -> List[Dict[str, Any]]:
        """
        Item 7: Computes rigorous, separate metrics for Manual_Paper_Execution vs Algorithmic strategies.
        No mixing, zero false credit.
        """
        resolved_db = db_path or str(PROJECT_ROOT / "kripto_agent.db")
        if not os.path.exists(resolved_db):
            return []

        try:
            uri = Path(resolved_db).as_posix()
            conn = sqlite3.connect(f"file:{uri}?mode=ro", uri=True)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("SELECT * FROM paper_positions WHERE status='CLOSED' ORDER BY closed_at ASC")
            rows = [dict(r) for r in cur.fetchall()]
            conn.close()
        except Exception as e:
            logger.error(f"PerformanceAttribution error: {e}")
            return []

        buckets: Dict[str, List[Dict[str, Any]]] = {}
        for r in rows:
            raw_strat = r.get("strategy") or ""
            raw_lower = raw_strat.lower()
            if not raw_strat or "manual" in raw_lower:
                cat = "Manual_Paper_Execution"
            elif "turbo" in raw_lower:
                cat = "turbo_fast_strike"
            elif "momentum" in raw_lower:
                cat = "momentum_dip_rebound"
            elif "bollinger" in raw_lower:
                cat = "bollinger_volume_breakout"
            elif "r10" in raw_lower or "rsi" in raw_lower:
                cat = "r10_rsi_divergence"
            elif "ema" in raw_lower:
                cat = "ema_macd_pullback"
            else:
                cat = raw_strat

            buckets.setdefault(cat, []).append(r)

        priority_order = [
            "Manual_Paper_Execution",
            "turbo_fast_strike",
            "momentum_dip_rebound",
            "bollinger_volume_breakout",
            "r10_rsi_divergence",
            "ema_macd_pullback",
        ]
        all_keys = list(priority_order) + [k for k in buckets.keys() if k not in priority_order]
        result = []

        for strat_name in all_keys:
            strat_rows = buckets.get(strat_name, [])
            n = len(strat_rows)
            if n == 0:
                result.append({
                    "strategy": strat_name,
                    "closed_trades": 0,
                    "wins": 0,
                    "losses": 0,
                    "win_rate": 0.0,
                    "gross_profit": 0.0,
                    "gross_loss": 0.0,
                    "commission": 0.0,
                    "slippage": 0.0,
                    "net_realized_pnl": 0.0,
                    "profit_factor": 0.0,
                    "expectancy_per_trade": 0.0,
                    "max_drawdown": 0.0,
                    "start_time": None,
                    "end_time": None,
                    "status": "INSUFFICIENT_SAMPLE",
                })
                continue

            pnls = [float(r.get("realized_pnl") or 0.0) for r in strat_rows]
            fees = [float(r.get("fees_paid") or 0.0) for r in strat_rows]
            wins = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p <= 0]
            win_count = len(wins)
            loss_count = len(losses)
            win_rate = round((win_count / n) * 100.0, 2)

            gross_profit = round(sum(wins), 2)
            gross_loss = round(abs(sum(losses)), 2)
            tot_fees = round(sum(fees), 2)
            net_pnl = round(sum(pnls), 2)

            profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else (10.0 if gross_profit > 0 else 0.0)
            expectancy = round(net_pnl / n, 2) if n > 0 else 0.0

            cum_pnl = 0.0
            peak = 0.0
            max_dd = 0.0
            for p in pnls:
                cum_pnl += p
                if cum_pnl > peak:
                    peak = cum_pnl
                dd = peak - cum_pnl
                if dd > max_dd:
                    max_dd = dd

            init_cap = float(initial_capital) if initial_capital > 0 else 10000.0
            max_dd_pct = round((max_dd / init_cap) * 100.0, 2)

            start_time = strat_rows[0].get("opened_at")
            end_time = strat_rows[-1].get("closed_at")

            if strat_name == "Manual_Paper_Execution":
                status = "MANUAL_NON_ALGORITHMIC"
            elif n < 30:
                status = "INSUFFICIENT_SAMPLE"
            elif net_pnl < 0 or profit_factor <= 1.0:
                status = "NEGATIVE_EDGE"
            elif profit_factor >= 1.30 and max_dd_pct < 15.0:
                status = "VALIDATED"
            else:
                status = "PAPER_VALIDATION"

            result.append({
                "strategy": strat_name,
                "closed_trades": n,
                "wins": win_count,
                "losses": loss_count,
                "win_rate": win_rate,
                "gross_profit": gross_profit,
                "gross_loss": gross_loss,
                "commission": tot_fees,
                "slippage": 0.0,
                "net_realized_pnl": net_pnl,
                "profit_factor": profit_factor,
                "expectancy_per_trade": expectancy,
                "max_drawdown": round(max_dd, 2),
                "max_drawdown_pct": max_dd_pct,
                "start_time": start_time,
                "end_time": end_time,
                "status": status,
            })

        return result
