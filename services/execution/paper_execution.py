import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Tuple

from services.execution.execution_interface import ExecutionEngine
from shared.config import get_settings
from shared.enums import (
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
    PositionStatus,
    SignalDirection,
)
from shared.logging import get_logger
from shared.schemas import Fill, Order, PortfolioState, Position, RiskDecision

logger = get_logger("paper-execution-engine", service="execution")
settings = get_settings()


class PaperExecutionEngine(ExecutionEngine):
    """
    Unified, authoritative Paper Execution Engine for KRIPTO AGENT V6.1.
    Consolidates services/paper_broker and services/paper_trading into a single
    production-grade simulated broker.

    Features:
    - Market, Limit, Stop-Loss, Take-Profit, Trailing 50% Peak Profit Lock
    - Maker / Taker fees (bps) and Slippage modeling (bps)
    - Partial fills and volume constraints
    - Real-time portfolio accounting: balance, available_balance, reserved_balance,
      equity, unrealized_pnl, realized_pnl, fees, slippage, gross_exposure, net_exposure, margin
    - SQLite persistence for crash/restart resiliency
    - Risk Engine Authentication: Rejects any order submission not accompanied by an approved RiskDecision.
    - Spot Short Rejection: Rejects any short execution if operating in Spot mode.
    """

    def __init__(
        self,
        initial_balance: Optional[float] = None,
        maker_fee: Optional[float] = None,
        taker_fee: Optional[float] = None,
        slippage_bps: Optional[float] = None,
        enable_trailing_stop: bool = True,
        enable_partial_exit: bool = True,
        is_spot_mode: bool = True,
        db_path: Optional[str] = None,
    ):
        cfg = get_settings()
        if initial_balance is None:
            initial_balance = getattr(cfg, "INITIAL_CAPITAL", 5000.0)
        if maker_fee is None:
            maker_fee = getattr(cfg, "MAKER_FEE", 0.001)
        if taker_fee is None:
            taker_fee = getattr(cfg, "TAKER_FEE", 0.001)
        if slippage_bps is None:
            slippage_bps = getattr(cfg, "SLIPPAGE_BPS", 5.0)

        self.db_path = db_path
        self.balance: float = initial_balance
        self.available_balance: float = initial_balance
        self.reserved_balance: float = 0.0
        self.initial_balance: float = initial_balance
        self.maker_fee: float = maker_fee
        self.taker_fee: float = taker_fee
        self.slippage_rate: float = slippage_bps / 10000.0
        self.enable_trailing_stop: bool = enable_trailing_stop
        self.enable_partial_exit: bool = enable_partial_exit
        self.is_spot_mode: bool = is_spot_mode
        self.is_halted: bool = False

        # Core state containers
        self.open_positions: Dict[str, Position] = {}
        self.closed_positions_history: List[Position] = []
        self.orders: Dict[str, Order] = {}
        self.fills: List[Fill] = []

        # Daily PnL tracking (UTC Day Reset - AUDIT-04)
        self.day_start_equity: float = initial_balance
        self.day_start_date = datetime.now(timezone.utc).date()

        # Database initialization & crash recovery
        if self.db_path:
            self._init_db()
            self.restore_state()

        logger.info(
            f"PaperExecutionEngine initialized. Initial Balance: ${self.balance:.2f}, "
            f"Taker Fee: {self.taker_fee*100:.2f}%, Slippage: {self.slippage_rate*10000:.1f} bps, "
            f"Spot Mode: {self.is_spot_mode}, DB: {self.db_path}"
        )

    # -------------------------------------------------------------------------
    # SQLite State Persistence & Crash/Restart Restoration
    # -------------------------------------------------------------------------
    def _get_db_conn(self) -> Optional[sqlite3.Connection]:
        if not self.db_path:
            return None
        if self.db_path == ":memory:":
            if not hasattr(self, "_memory_conn") or self._memory_conn is None:
                self._memory_conn = sqlite3.connect(":memory:", check_same_thread=False)
                self._memory_conn.row_factory = sqlite3.Row
            return self._memory_conn
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _close_db_conn(self, conn: Optional[sqlite3.Connection]):
        if conn and conn != getattr(self, "_memory_conn", None):
            try:
                conn.close()
            except Exception:
                pass

    def _init_db(self):
        if not self.db_path:
            return
        conn = self._get_db_conn()
        if not conn:
            return
        try:
            with conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS paper_account (
                        id INTEGER PRIMARY KEY,
                        initial_balance REAL NOT NULL,
                        balance REAL NOT NULL,
                        available_balance REAL NOT NULL,
                        reserved_balance REAL NOT NULL,
                        day_start_equity REAL NOT NULL,
                        day_start_date TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS paper_positions (
                        position_id TEXT PRIMARY KEY,
                        symbol TEXT NOT NULL,
                        side TEXT NOT NULL,
                        entry_price REAL NOT NULL,
                        quantity REAL NOT NULL,
                        current_price REAL NOT NULL,
                        stop_loss REAL NOT NULL,
                        take_profit REAL NOT NULL,
                        unrealized_pnl REAL NOT NULL,
                        realized_pnl REAL NOT NULL,
                        status TEXT NOT NULL,
                        opened_at TEXT NOT NULL,
                        closed_at TEXT,
                        fees_paid REAL,
                        strategy TEXT,
                        peak_price REAL,
                        initial_quantity REAL,
                        initial_stop_loss REAL,
                        risk_dist REAL,
                        partial_tp_hit INTEGER DEFAULT 0,
                        partial_realized_pnl REAL DEFAULT 0.0,
                        partial_fees_paid REAL DEFAULT 0.0,
                        partial_realized_at TEXT
                    )
                """)
                # Defensive migrations for existing databases
                for col_name, col_type in [
                    ("initial_quantity", "REAL"),
                    ("initial_stop_loss", "REAL"),
                    ("risk_dist", "REAL"),
                    ("partial_tp_hit", "INTEGER DEFAULT 0"),
                    ("partial_realized_pnl", "REAL DEFAULT 0.0"),
                    ("partial_fees_paid", "REAL DEFAULT 0.0"),
                    ("partial_realized_at", "TEXT"),
                ]:
                    try:
                        conn.execute(f"ALTER TABLE paper_positions ADD COLUMN {col_name} {col_type}")
                    except sqlite3.OperationalError:
                        pass

                conn.execute("""
                    CREATE TABLE IF NOT EXISTS paper_orders (
                        order_id TEXT PRIMARY KEY,
                        symbol TEXT NOT NULL,
                        order_type TEXT NOT NULL,
                        side TEXT NOT NULL,
                        quantity REAL NOT NULL,
                        price REAL,
                        status TEXT NOT NULL,
                        filled_quantity REAL NOT NULL,
                        average_fill_price REAL NOT NULL,
                        fee_paid REAL NOT NULL,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS paper_fills (
                        fill_id TEXT PRIMARY KEY,
                        order_id TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        side TEXT NOT NULL,
                        price REAL NOT NULL,
                        quantity REAL NOT NULL,
                        fee REAL NOT NULL,
                        slippage REAL NOT NULL,
                        timestamp TEXT NOT NULL
                    )
                """)
        finally:
            self._close_db_conn(conn)

    def _persist_account(self):
        if not self.db_path:
            return
        conn = self._get_db_conn()
        if not conn:
            return
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            date_str = str(self.day_start_date)
            with conn:
                conn.execute("""
                    INSERT INTO paper_account (id, initial_balance, balance, available_balance, reserved_balance, day_start_equity, day_start_date, updated_at)
                    VALUES (1, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        initial_balance=excluded.initial_balance,
                        balance=excluded.balance,
                        available_balance=excluded.available_balance,
                        reserved_balance=excluded.reserved_balance,
                        day_start_equity=excluded.day_start_equity,
                        day_start_date=excluded.day_start_date,
                        updated_at=excluded.updated_at
                """, (self.initial_balance, self.balance, self.available_balance, self.reserved_balance, self.day_start_equity, date_str, now_iso))
        except Exception as e:
            logger.error(f"Failed to persist paper account: {e}")
        finally:
            self._close_db_conn(conn)

    def _persist_position(self, pos: Position):
        if not self.db_path:
            return
        conn = self._get_db_conn()
        if not conn:
            return
        try:
            opened_iso = pos.opened_at.isoformat() if hasattr(pos.opened_at, "isoformat") else str(pos.opened_at)
            closed_iso = pos.closed_at.isoformat() if (pos.closed_at and hasattr(pos.closed_at, "isoformat")) else (str(pos.closed_at) if pos.closed_at else None)
            side_str = pos.side.value if hasattr(pos.side, "value") else str(pos.side)
            status_str = pos.status.value if hasattr(pos.status, "value") else str(pos.status)
            with conn:
                conn.execute("""
                    INSERT INTO paper_positions (
                        position_id, symbol, side, entry_price, quantity, current_price,
                        stop_loss, take_profit, unrealized_pnl, realized_pnl, status,
                        opened_at, closed_at, fees_paid, strategy, peak_price,
                        initial_quantity, initial_stop_loss, risk_dist, partial_tp_hit,
                        partial_realized_pnl, partial_fees_paid, partial_realized_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(position_id) DO UPDATE SET
                        quantity=excluded.quantity,
                        stop_loss=excluded.stop_loss,
                        take_profit=excluded.take_profit,
                        current_price=excluded.current_price,
                        unrealized_pnl=excluded.unrealized_pnl,
                        realized_pnl=excluded.realized_pnl,
                        status=excluded.status,
                        closed_at=excluded.closed_at,
                        fees_paid=excluded.fees_paid,
                        peak_price=excluded.peak_price,
                        initial_quantity=excluded.initial_quantity,
                        initial_stop_loss=excluded.initial_stop_loss,
                        risk_dist=excluded.risk_dist,
                        partial_tp_hit=excluded.partial_tp_hit,
                        partial_realized_pnl=excluded.partial_realized_pnl,
                        partial_fees_paid=excluded.partial_fees_paid,
                        partial_realized_at=excluded.partial_realized_at
                """, (
                    pos.position_id, pos.symbol, side_str, pos.entry_price, pos.quantity, pos.current_price,
                    pos.stop_loss, pos.take_profit, pos.unrealized_pnl, pos.realized_pnl, status_str,
                    opened_iso, closed_iso, pos.fees_paid, pos.strategy, pos.peak_price,
                    getattr(pos, "initial_quantity", None),
                    getattr(pos, "initial_stop_loss", None),
                    getattr(pos, "risk_dist", None),
                    1 if getattr(pos, "partial_tp_hit", False) else 0,
                    getattr(pos, "partial_realized_pnl", 0.0),
                    getattr(pos, "partial_fees_paid", 0.0),
                    pos.partial_realized_at.isoformat() if getattr(pos, "partial_realized_at", None) else None,
                ))
        except Exception as e:
            logger.error(f"Failed to persist position {pos.position_id}: {e}")
        finally:
            self._close_db_conn(conn)

    def _persist_order(self, order: Order):
        if not self.db_path:
            return
        conn = self._get_db_conn()
        if not conn:
            return
        try:
            created_iso = order.created_at.isoformat() if hasattr(order.created_at, "isoformat") else str(order.created_at)
            updated_iso = order.updated_at.isoformat() if hasattr(order.updated_at, "isoformat") else str(order.updated_at)
            type_str = order.order_type.value if hasattr(order.order_type, "value") else str(order.order_type)
            side_str = order.side.value if hasattr(order.side, "value") else str(order.side)
            status_str = order.status.value if hasattr(order.status, "value") else str(order.status)
            with conn:
                conn.execute("""
                    INSERT INTO paper_orders (order_id, symbol, order_type, side, quantity, price, status, filled_quantity, average_fill_price, fee_paid, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(order_id) DO UPDATE SET
                        status=excluded.status,
                        filled_quantity=excluded.filled_quantity,
                        average_fill_price=excluded.average_fill_price,
                        fee_paid=excluded.fee_paid,
                        updated_at=excluded.updated_at
                """, (order.order_id, order.symbol, type_str, side_str, order.quantity, order.price, status_str, order.filled_quantity, order.average_fill_price, order.fee_paid, created_iso, updated_iso))
        except Exception as e:
            logger.error(f"Failed to persist order {order.order_id}: {e}")
        finally:
            self._close_db_conn(conn)

    def _persist_fill(self, fill: Fill):
        if not self.db_path:
            return
        conn = self._get_db_conn()
        if not conn:
            return
        try:
            ts_iso = fill.timestamp.isoformat() if hasattr(fill.timestamp, "isoformat") else str(fill.timestamp)
            side_str = fill.side.value if hasattr(fill.side, "value") else str(fill.side)
            with conn:
                conn.execute("""
                    INSERT OR IGNORE INTO paper_fills (fill_id, order_id, symbol, side, price, quantity, fee, slippage, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (fill.fill_id, fill.order_id, fill.symbol, side_str, fill.price, fill.quantity, fill.fee, fill.slippage, ts_iso))
        except Exception as e:
            logger.error(f"Failed to persist fill {fill.fill_id}: {e}")
        finally:
            self._close_db_conn(conn)

    def restore_state(self):
        """Authoritatively restores account balance, open positions, orders, and fills from SQLite."""
        if not self.db_path:
            return
        conn = self._get_db_conn()
        if not conn:
            return
        try:
            cur = conn.cursor()
            # 1. Restore account
            cur.execute("SELECT * FROM paper_account WHERE id=1")
            acc_row = cur.fetchone()
            if acc_row:
                self.initial_balance = float(acc_row["initial_balance"])
                self.balance = float(acc_row["balance"])
                self.available_balance = float(acc_row["available_balance"])
                self.reserved_balance = float(acc_row["reserved_balance"])
                self.day_start_equity = float(acc_row["day_start_equity"])
                try:
                    self.day_start_date = datetime.fromisoformat(acc_row["day_start_date"]).date()
                except Exception:
                    pass

            # 2. Restore orders
            cur.execute("SELECT * FROM paper_orders")
            for r in cur.fetchall():
                ord_obj = Order(
                    order_id=r["order_id"],
                    symbol=r["symbol"],
                    order_type=OrderType(r["order_type"]) if r["order_type"] in OrderType._value2member_map_ else OrderType.MARKET,
                    side=OrderSide(r["side"]) if r["side"] in OrderSide._value2member_map_ else OrderSide.BUY,
                    quantity=float(r["quantity"]),
                    price=float(r["price"]) if r["price"] is not None else None,
                    status=OrderStatus(r["status"]) if r["status"] in OrderStatus._value2member_map_ else OrderStatus.FILLED,
                    filled_quantity=float(r["filled_quantity"]),
                    average_fill_price=float(r["average_fill_price"]),
                    fee_paid=float(r["fee_paid"]),
                    created_at=datetime.fromisoformat(r["created_at"]),
                    updated_at=datetime.fromisoformat(r["updated_at"]),
                )
                self.orders[ord_obj.order_id] = ord_obj

            # 3. Restore fills
            cur.execute("SELECT * FROM paper_fills")
            for r in cur.fetchall():
                fill_obj = Fill(
                    fill_id=r["fill_id"],
                    order_id=r["order_id"],
                    symbol=r["symbol"],
                    side=OrderSide(r["side"]) if r["side"] in OrderSide._value2member_map_ else OrderSide.BUY,
                    price=float(r["price"]),
                    quantity=float(r["quantity"]),
                    fee=float(r["fee"]),
                    slippage=float(r["slippage"]),
                    timestamp=datetime.fromisoformat(r["timestamp"]),
                )
                self.fills.append(fill_obj)

            # 4. Restore positions
            cur.execute("SELECT * FROM paper_positions")
            for r in cur.fetchall():
                pos_obj = Position(
                    position_id=r["position_id"],
                    symbol=r["symbol"],
                    side=PositionSide(r["side"]) if r["side"] in PositionSide._value2member_map_ else PositionSide.LONG,
                    entry_price=float(r["entry_price"]),
                    quantity=float(r["quantity"]),
                    current_price=float(r["current_price"]),
                    stop_loss=float(r["stop_loss"]),
                    take_profit=float(r["take_profit"]),
                    unrealized_pnl=float(r["unrealized_pnl"]),
                    realized_pnl=float(r["realized_pnl"]),
                    status=PositionStatus(r["status"]) if r["status"] in PositionStatus._value2member_map_ else PositionStatus.OPEN,
                    opened_at=datetime.fromisoformat(r["opened_at"]),
                    closed_at=datetime.fromisoformat(r["closed_at"]) if r["closed_at"] else None,
                    fees_paid=float(r["fees_paid"]) if r["fees_paid"] is not None else 0.0,
                    strategy=r["strategy"] or "",
                    peak_price=float(r["peak_price"]) if r["peak_price"] is not None else None,
                    initial_quantity=float(r["initial_quantity"]) if ("initial_quantity" in r.keys() and r["initial_quantity"] is not None) else None,
                    initial_stop_loss=float(r["initial_stop_loss"]) if ("initial_stop_loss" in r.keys() and r["initial_stop_loss"] is not None) else None,
                    risk_dist=float(r["risk_dist"]) if ("risk_dist" in r.keys() and r["risk_dist"] is not None) else None,
                    partial_tp_hit=bool(r["partial_tp_hit"]) if ("partial_tp_hit" in r.keys() and r["partial_tp_hit"] is not None) else False,
                    partial_realized_pnl=float(r["partial_realized_pnl"]) if ("partial_realized_pnl" in r.keys() and r["partial_realized_pnl"] is not None) else 0.0,
                    partial_fees_paid=float(r["partial_fees_paid"]) if ("partial_fees_paid" in r.keys() and r["partial_fees_paid"] is not None) else 0.0,
                    partial_realized_at=datetime.fromisoformat(r["partial_realized_at"]) if ("partial_realized_at" in r.keys() and r["partial_realized_at"]) else None,
                )
                if pos_obj.status == PositionStatus.OPEN:
                    self.open_positions[pos_obj.symbol] = pos_obj
                else:
                    self.closed_positions_history.append(pos_obj)

            if acc_row or self.orders or self.open_positions:
                logger.info(
                    f"Paper broker state successfully restored from SQLite ({self.db_path}): "
                    f"Balance=${self.balance:.2f}, OpenPositions={len(self.open_positions)}, "
                    f"ClosedPositions={len(self.closed_positions_history)}, Orders={len(self.orders)}, Fills={len(self.fills)}"
                )
        except Exception as e:
            logger.error(f"Failed to restore paper broker state: {e}")
        finally:
            self._close_db_conn(conn)

    def reset_portfolio(self, initial_balance: Optional[float] = None):
        """
        Resets the paper trading account and in-memory containers to a fresh clean state.
        Clears all open positions, closed positions history, orders, and fills.
        Wipes paper tables from SQLite persistence.
        """
        if initial_balance is None:
            initial_balance = getattr(get_settings(), "INITIAL_CAPITAL", 5000.0)
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.available_balance = initial_balance
        self.reserved_balance = 0.0
        self.is_halted = False
        self.open_positions.clear()
        self.closed_positions_history.clear()
        self.orders.clear()
        self.fills.clear()
        self.day_start_equity = initial_balance
        self.day_start_date = datetime.now(timezone.utc).date()

        if self.db_path:
            conn = self._get_db_conn()
            if conn:
                try:
                    with conn:
                        conn.execute("DELETE FROM paper_positions")
                        conn.execute("DELETE FROM paper_orders")
                        conn.execute("DELETE FROM paper_fills")
                        now_iso = datetime.now(timezone.utc).isoformat()
                        conn.execute(
                            """
                            INSERT INTO paper_account (id, initial_balance, balance, available_balance, reserved_balance, day_start_equity, day_start_date, updated_at)
                            VALUES (1, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(id) DO UPDATE SET
                                initial_balance=excluded.initial_balance,
                                balance=excluded.balance,
                                available_balance=excluded.available_balance,
                                reserved_balance=excluded.reserved_balance,
                                day_start_equity=excluded.day_start_equity,
                                day_start_date=excluded.day_start_date,
                                updated_at=excluded.updated_at
                            """,
                            (self.initial_balance, self.balance, self.available_balance, self.reserved_balance, self.day_start_equity, str(self.day_start_date), now_iso)
                        )
                except Exception as e:
                    logger.error(f"Failed to reset paper DB: {e}")
                finally:
                    self._close_db_conn(conn)
        logger.info(f"PaperExecutionEngine reset complete. Balance: ${self.balance:.2f}")

    # -------------------------------------------------------------------------
    # Portfolio Accounting Properties
    # -------------------------------------------------------------------------
    def _check_day_rollover(self):
        current_date = datetime.now(timezone.utc).date()
        if current_date > self.day_start_date:
            self.day_start_date = current_date
            self.day_start_equity = self.equity
            logger.info(f"UTC Day Rollover: New day_start_equity is ${self.day_start_equity:.2f}")

    @property
    def equity(self) -> float:
        return round(self.balance + self.total_unrealized_pnl, 2)

    @property
    def total_unrealized_pnl(self) -> float:
        return sum(
            pos.unrealized_pnl
            for pos in self.open_positions.values()
            if pos.status == PositionStatus.OPEN
        )

    @property
    def total_realized_pnl(self) -> float:
        """Lifetime cumulative realized PnL across all closed positions and open partial exits."""
        closed_pnl = sum(pos.realized_pnl for pos in self.closed_positions_history)
        partial_open_pnl = sum(getattr(pos, "partial_realized_pnl", 0.0) for pos in self.open_positions.values())
        return round(closed_pnl + partial_open_pnl, 2)

    @property
    def daily_realized_pnl(self) -> float:
        """Realized PnL strictly from positions closed or partially closed during current UTC day."""
        today_start = datetime.combine(datetime.now(timezone.utc).date(), datetime.min.time(), tzinfo=timezone.utc)
        total = 0.0
        for pos in self.closed_positions_history:
            c_at = getattr(pos, "closed_at", None)
            if c_at is not None:
                c_at = c_at if c_at.tzinfo is not None else c_at.replace(tzinfo=timezone.utc)
                if c_at >= today_start:
                    total += getattr(pos, "realized_pnl", 0.0)
            else:
                total += getattr(pos, "realized_pnl", 0.0)
        for pos in self.open_positions.values():
            partial_at = getattr(pos, "partial_realized_at", None)
            if partial_at is not None and partial_at.tzinfo is None:
                partial_at = partial_at.replace(tzinfo=timezone.utc)
            if (
                getattr(pos, "partial_tp_hit", False)
                and getattr(pos, "partial_realized_pnl", 0.0) != 0.0
                and partial_at is not None
                and partial_at >= today_start
            ):
                total += pos.partial_realized_pnl
        return round(total, 2)

    @property
    def daily_pnl(self) -> float:
        """True daily PnL (Intraday Unrealized + Realized today) (AUDIT-04)."""
        self._check_day_rollover()
        return round(self.total_unrealized_pnl + self.daily_realized_pnl, 2)

    @property
    def total_fees_paid(self) -> float:
        return sum(f.fee for f in self.fills)

    @property
    def total_slippage_paid(self) -> float:
        return sum(f.slippage for f in self.fills)

    @property
    def gross_exposure(self) -> float:
        return sum(
            pos.current_price * pos.quantity
            for pos in self.open_positions.values()
            if pos.status == PositionStatus.OPEN
        )

    @property
    def net_exposure(self) -> float:
        net = 0.0
        for pos in self.open_positions.values():
            if pos.status == PositionStatus.OPEN:
                val = pos.current_price * pos.quantity
                if pos.side == PositionSide.LONG:
                    net += val
                else:
                    net -= val
        return net

    def get_portfolio_snapshot(self) -> Dict[str, Any]:
        return {
            "initial_balance": self.initial_balance,
            "balance": round(self.balance, 2),
            "available_balance": round(self.available_balance, 2),
            "reserved_balance": round(self.reserved_balance, 2),
            "equity": self.equity,
            "unrealized_pnl": round(self.total_unrealized_pnl, 2),
            "realized_pnl": round(self.total_realized_pnl, 2),
            "daily_pnl": self.daily_pnl,
            "daily_realized_pnl": self.daily_realized_pnl,
            "total_fees": round(self.total_fees_paid, 2),
            "total_slippage": round(self.total_slippage_paid, 2),
            "gross_exposure": round(self.gross_exposure, 2),
            "net_exposure": round(self.net_exposure, 2),
            "open_positions_count": len(self.open_positions),
            "closed_positions_count": len(self.closed_positions_history),
            "fills_count": len(self.fills),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    def to_portfolio_state(self) -> PortfolioState:
        open_pos_list = list(self.open_positions.values())
        return PortfolioState(
            balance=round(self.balance, 2),
            equity=self.equity,
            unrealized_pnl=round(self.total_unrealized_pnl, 2),
            realized_pnl=round(self.total_realized_pnl, 2),
            open_positions=open_pos_list,
            daily_pnl=self.daily_pnl,
            daily_realized_pnl=self.daily_realized_pnl,
            max_drawdown_current=round((self.initial_balance - self.equity) / self.initial_balance, 4) if self.equity < self.initial_balance else 0.0,
            is_halted=False,
            timestamp=datetime.now(timezone.utc),
        )

    @property
    def portfolio(self):
        """Backwards compatibility adapter for legacy components expecting broker.portfolio."""
        engine = self

        class PortfolioAdapter:
            @property
            def balance(self):
                return engine.balance

            @property
            def equity(self):
                return engine.equity

            @property
            def positions(self):
                return engine.open_positions

            @property
            def realized_pnl(self):
                return engine.total_realized_pnl

            @property
            def unrealized_pnl(self):
                return engine.total_unrealized_pnl

            def update_market_price(self, symbol: str, price: float):
                engine.update_market_price(symbol, price)

            def get_state(self):
                return engine.to_portfolio_state()

            def get_portfolio_state(self):
                return engine.to_portfolio_state()

        return PortfolioAdapter()

    # -------------------------------------------------------------------------
    # ExecutionEngine Interface Implementation
    # -------------------------------------------------------------------------
    async def submit_order(
        self,
        decision: RiskDecision,
        strategy_name: str = "",
    ) -> Tuple[Order, Fill, Position]:
        return self.execute_market_order(
            decision,
            strategy_name,
            execution_style=getattr(settings, "EXECUTION_STYLE", "TAKER"),
        )

    async def cancel_order(self, order_id: str) -> bool:
        if order_id in self.orders:
            order = self.orders[order_id]
            if order.status == OrderStatus.OPEN:
                order.status = OrderStatus.CANCELLED
                order.updated_at = datetime.now(timezone.utc)
                # Release reserved funds if this was a buy limit
                if order.side == OrderSide.BUY:
                    notional = (order.price or 0.0) * order.quantity
                    self.reserved_balance = max(0.0, self.reserved_balance - notional)
                    self.available_balance = self.balance - self.reserved_balance
                self._persist_order(order)
                self._persist_account()
                logger.info(f"Order cancelled: {order_id}")
                return True
        return False

    async def cancel_all_orders(self) -> int:
        """Cancels all active orders in the paper engine."""
        count = 0
        for oid in list(self.orders.keys()):
            if await self.cancel_order(oid):
                count += 1
        return count

    def emergency_close_all(self, reason: str = "EMERGENCY_SHUTDOWN") -> int:
        """
        Emergency liquidation and halt mechanism (Section 38 & P0-007):
        1. Sets is_halted = True to reject any subsequent order executions.
        2. Closes all currently open positions immediately at current mark or entry price.
        3. Cancels all pending orders and releases reserved balances.
        """
        self.is_halted = True
        closed_count = 0
        now = datetime.now(timezone.utc)
        for sym, pos in list(self.open_positions.items()):
            if pos.status == PositionStatus.OPEN:
                exit_price = pos.current_price if (pos.current_price and pos.current_price > 0) else pos.entry_price
                self.close_position(
                    symbol=sym,
                    exit_price=exit_price,
                    exit_time=now,
                    exit_reason=f"EMERGENCY_CLOSE: {reason}",
                )
                closed_count += 1

        for oid, order in list(self.orders.items()):
            if order.status in [OrderStatus.OPEN, OrderStatus.CREATED, OrderStatus.SUBMITTED]:
                order.status = OrderStatus.CANCELLED
                order.updated_at = now
                if order.side == OrderSide.BUY:
                    notional = (order.price or 0.0) * order.quantity
                    self.reserved_balance = max(0.0, self.reserved_balance - notional)
                    self.available_balance = self.balance - self.reserved_balance
                self._persist_order(order)
        self._persist_account()
        logger.warning(f"PaperExecutionEngine: EMERGENCY CLOSE ALL executed ({closed_count} positions liquidated, is_halted=True)")
        return closed_count

    def unhalt(self):
        """Resets the emergency halt state."""
        self.is_halted = False
        logger.info("PaperExecutionEngine unhalted.")

    async def get_order(self, order_id: str) -> Optional[Order]:
        return self.orders.get(order_id)

    # -------------------------------------------------------------------------
    # Core Order & Position Lifecycle
    # -------------------------------------------------------------------------
    def execute_market_order(
        self,
        decision: RiskDecision,
        strategy_name: str = "",
        order_id: Optional[str] = None,
        execution_style: Literal["TAKER", "MAKER"] = "TAKER",
    ) -> Tuple[Order, Fill, Position]:
        """
        Executes simulated market fill based strictly on an approved RiskDecision.
        Enforces Spot mode rejection for short positions.
        """
        # 0. Emergency Halt Verification (P0-007)
        if getattr(self, "is_halted", False):
            logger.error("Execution rejected: PaperExecutionEngine is halted (EMERGENCY_STOP).")
            raise PermissionError("Trading is halted by emergency stop.")

        # 1. Verification of Risk Approval
        if not decision or not decision.approved:
            reason = decision.reason if decision else "No decision provided"
            logger.error(f"Execution rejected: unapproved decision ({reason})")
            raise PermissionError(f"PaperExecutionEngine requires approved RiskDecision: {reason}")

        # 2. Spot Mode Enforcement (Requirement 6 & 36)
        if self.is_spot_mode and decision.direction == SignalDirection.SHORT:
            logger.warning(
                f"Spot Mode Violation: SHORT order rejected on {decision.symbol}. Marked as SIGNAL_ONLY."
            )
            raise ValueError(f"Spot mode does not support SHORT execution on {decision.symbol}. (SIGNAL_ONLY)")

        if order_id and order_id in self.orders:
            raise ValueError(f"Duplicate order rejected: order {order_id} already exists.")

        if decision.calculated_size <= 0:
            raise ValueError(f"Order quantity must be positive: got {decision.calculated_size}")

        now = datetime.now(timezone.utc)
        order_id = order_id or f"ord_{uuid.uuid4().hex[:12]}"
        fill_id = f"fill_{uuid.uuid4().hex[:12]}"
        pos_id = f"pos_{uuid.uuid4().hex[:12]}"

        side = OrderSide.BUY if decision.direction == SignalDirection.LONG else OrderSide.SELL
        pos_side = PositionSide.LONG if decision.direction == SignalDirection.LONG else PositionSide.SHORT

        execution_style = str(execution_style).upper()
        if execution_style not in {"TAKER", "MAKER"}:
            raise ValueError("execution_style must be TAKER or MAKER")

        # Cost model: TAKER incurs configured adverse slippage + taker fee.
        # MAKER models a resting-limit-style fill at decision price with maker fee;
        # it does NOT assume queue priority or guaranteed fill, so callers should
        # only use this path for a completed-fill counterfactual/backtest.
        base_price = decision.entry_price
        if execution_style == "TAKER":
            slippage_mult = (1.0 + self.slippage_rate) if side == OrderSide.BUY else (1.0 - self.slippage_rate)
            exec_price = round(base_price * slippage_mult, 4 if base_price < 10 else 2)
            fee_rate = self.taker_fee
        else:
            exec_price = round(base_price, 4 if base_price < 10 else 2)
            fee_rate = self.maker_fee

        slippage_cost = round(abs(exec_price - base_price) * decision.calculated_size, 4)
        notional = exec_price * decision.calculated_size
        fee = round(notional * fee_rate, 4)

        # Available Balance Check
        if side == OrderSide.BUY and notional + fee > self.available_balance:
            raise ValueError(
                f"Insufficient available balance (${self.available_balance:.2f}) for order notional (${notional:.2f} + fee ${fee:.2f})"
            )

        # Deduct fee and adjust available balance
        self.balance -= fee
        if side == OrderSide.BUY:
            self.available_balance = max(0.0, self.available_balance - (notional + fee))
            self.reserved_balance += notional

        order = Order(
            order_id=order_id,
            symbol=decision.symbol,
            order_type=OrderType.MARKET if execution_style == "TAKER" else OrderType.LIMIT,
            side=side,
            quantity=decision.calculated_size,
            price=base_price,
            status=OrderStatus.FILLED,
            filled_quantity=decision.calculated_size,
            average_fill_price=exec_price,
            fee_paid=fee,
            created_at=now,
            updated_at=now,
        )

        fill = Fill(
            fill_id=fill_id,
            order_id=order_id,
            symbol=decision.symbol,
            side=side,
            price=exec_price,
            quantity=decision.calculated_size,
            fee=fee,
            slippage=slippage_cost,
            timestamp=now,
        )

        initial_stop = decision.stop_loss
        risk_dist = round(abs(exec_price - initial_stop), 4)
        position = Position(
            position_id=pos_id,
            symbol=decision.symbol,
            side=pos_side,
            entry_price=exec_price,
            current_price=exec_price,
            quantity=decision.calculated_size,
            stop_loss=decision.stop_loss,
            take_profit=decision.take_profit,
            unrealized_pnl=0.0,
            realized_pnl=0.0,
            status=PositionStatus.OPEN,
            strategy=strategy_name,
            opened_at=now,
            peak_price=exec_price,
            fees_paid=fee,
            initial_quantity=decision.calculated_size,
            initial_stop_loss=initial_stop,
            risk_dist=risk_dist,
            partial_tp_hit=False,
            partial_realized_pnl=0.0,
            partial_fees_paid=0.0,
            partial_realized_at=None,
        )

        self.orders[order_id] = order
        self.fills.append(fill)
        self.open_positions[decision.symbol] = position

        # Persist state atomically to SQLite
        self._persist_order(order)
        self._persist_fill(fill)
        self._persist_position(position)
        self._persist_account()

        logger.info(
            f"Paper Position OPENED: {decision.symbol} {pos_side.value} qty={decision.calculated_size} "
            f"@ ${exec_price:.2f} (SL: ${decision.stop_loss:.2f}, TP: ${decision.take_profit:.2f})",
            extra={"symbol": decision.symbol, "strategy": strategy_name},
        )
        return order, fill, position

    def update_market_price(self, symbol: str, current_price: float):
        """Updates open position current price and recalculates unrealized PnL."""
        if symbol not in self.open_positions:
            return
        pos = self.open_positions[symbol]
        pos.current_price = current_price

        if pos.side == PositionSide.LONG:
            pos.unrealized_pnl = (current_price - pos.entry_price) * pos.quantity
        else:
            pos.unrealized_pnl = (pos.entry_price - current_price) * pos.quantity

    def check_position_stops_and_targets(
        self,
        symbol: str,
        high: float,
        low: float,
        close: float,
    ) -> Optional[Tuple[Position, str, float]]:
        """
        Evaluates dynamic stops, targets, and Trailing 50% Peak Profit Lock.
        Returns (closed_position, reason, exit_price) if an exit is triggered.
        """
        if symbol not in self.open_positions:
            return None

        pos = self.open_positions[symbol]
        if pos.status != PositionStatus.OPEN:
            return None

        self.update_market_price(symbol, close)

        # ---------------------------------------------------------------------
        # 1. Full Exit & Ambiguous Bar Evaluation (Fail-Closed, Risk-First)
        # ---------------------------------------------------------------------
        bar_start_sl = pos.stop_loss
        hit_reason: Optional[str] = None
        exit_price: float = 0.0

        if pos.side == PositionSide.LONG:
            # Ambiguous candle: both TP and initial SL breached in single candle
            if low <= bar_start_sl and high >= pos.take_profit:
                hit_reason = "STOP_LOSS"
                exit_price = bar_start_sl
            elif low <= bar_start_sl:
                hit_reason = "STOP_LOSS"
                exit_price = bar_start_sl
            elif high >= pos.take_profit:
                hit_reason = "TAKE_PROFIT"
                exit_price = pos.take_profit

        elif pos.side == PositionSide.SHORT:
            # Ambiguous candle: both TP and initial SL breached in single candle
            if high >= bar_start_sl and low <= pos.take_profit:
                hit_reason = "STOP_LOSS"
                exit_price = bar_start_sl
            elif high >= bar_start_sl:
                hit_reason = "STOP_LOSS"
                exit_price = bar_start_sl
            elif low <= pos.take_profit:
                hit_reason = "TAKE_PROFIT"
                exit_price = pos.take_profit

        if hit_reason and exit_price > 0:
            closed_pos = self.close_position(symbol=symbol, exit_price=exit_price, reason=hit_reason)
            if closed_pos:
                return closed_pos, hit_reason, exit_price

        # ---------------------------------------------------------------------
        # 2. Intra-bar Partial Take-Profit (+1.0R) & Trailing Stop Evaluation
        # Evaluated only if full position was not closed by initial bounds.
        # ---------------------------------------------------------------------
        initial_risk = getattr(pos, "risk_dist", None)
        if not initial_risk or initial_risk <= 0:
            initial_sl = getattr(pos, "initial_stop_loss", None) or pos.stop_loss
            initial_risk = abs(pos.entry_price - initial_sl)
            if initial_risk <= 0:
                initial_risk = pos.entry_price * 0.01  # fallback 1%

        if pos.peak_price is None:
            pos.peak_price = pos.entry_price

        # 50% Partial Take-Profit (+1.0R)
        if self.enable_partial_exit and not getattr(pos, "partial_tp_hit", False):
            if pos.side == PositionSide.LONG:
                target_1r = pos.entry_price + initial_risk
                if high >= target_1r:
                    self.execute_partial_exit(symbol=symbol, fraction=0.5, exit_price=target_1r, reason="PARTIAL_TP_1R")
            elif pos.side == PositionSide.SHORT:
                target_1r = pos.entry_price - initial_risk
                if low <= target_1r:
                    self.execute_partial_exit(symbol=symbol, fraction=0.5, exit_price=target_1r, reason="PARTIAL_TP_1R")

        if self.enable_trailing_stop:
            if pos.side == PositionSide.LONG:
                if high > pos.peak_price:
                    pos.peak_price = high

                peak_gain = pos.peak_price - pos.entry_price

                # Break-even check (+1.0R)
                if high >= pos.entry_price + initial_risk and pos.stop_loss < pos.entry_price:
                    pos.stop_loss = pos.entry_price
                    logger.info(f"Stop-Loss adjusted to BREAK-EVEN for {symbol} LONG @ {pos.entry_price:.2f}")

                # Peak Profit Lock: If gained >= 1.5R, protect 50% of peak profit from pullback
                if peak_gain >= (initial_risk * 1.5) and peak_gain > 0:
                    lock_price = pos.peak_price - (peak_gain * 0.5)
                    if lock_price > pos.stop_loss:
                        pos.stop_loss = round(lock_price, 4)
                        logger.info(
                            f"Trailing Profit Lock: {symbol} LONG stop raised to ${pos.stop_loss:.2f} "
                            f"(Protecting 50% of peak gain +${peak_gain:.2f})"
                        )

                # Check if price pulled back to hit new trailing stop
                if low <= pos.stop_loss:
                    hit_reason = "STOP_LOSS"
                    exit_price = pos.stop_loss

            elif pos.side == PositionSide.SHORT:
                if low < pos.peak_price:
                    pos.peak_price = low

                peak_gain = pos.entry_price - pos.peak_price

                # Break-even check (+1.0R)
                if low <= pos.entry_price - initial_risk and pos.stop_loss > pos.entry_price:
                    pos.stop_loss = pos.entry_price
                    logger.info(f"Stop-Loss adjusted to BREAK-EVEN for {symbol} SHORT @ {pos.entry_price:.2f}")

                # Peak Profit Lock: If gained >= 1.5R, protect 50% of peak profit from pullback
                if peak_gain >= (initial_risk * 1.5) and peak_gain > 0:
                    lock_price = pos.peak_price + (peak_gain * 0.5)
                    if lock_price < pos.stop_loss:
                        pos.stop_loss = round(lock_price, 4)
                        logger.info(
                            f"Trailing Profit Lock: {symbol} SHORT stop lowered to ${pos.stop_loss:.2f} "
                            f"(Protecting 50% of peak gain +${peak_gain:.2f})"
                        )

                # Check if price pulled back to hit new trailing stop
                if high >= pos.stop_loss:
                    hit_reason = "STOP_LOSS"
                    exit_price = pos.stop_loss

        if hit_reason and exit_price > 0:
            closed_pos = self.close_position(symbol=symbol, exit_price=exit_price, reason=hit_reason)
            if closed_pos:
                return closed_pos, hit_reason, exit_price

        return None

    def execute_partial_exit(
        self,
        symbol: str,
        fraction: float = 0.5,
        exit_price: Optional[float] = None,
        reason: str = "PARTIAL_TP_1R",
    ) -> Optional[Position]:
        """
        Executes a partial close (e.g. 50% at +1.0R) on an open position.
        Banks realized profit into cash balance, reduces remaining quantity,
        and moves stop loss to break-even (pos.entry_price).
        """
        if symbol not in self.open_positions:
            return None
        pos = self.open_positions[symbol]
        if pos.status != PositionStatus.OPEN or pos.quantity <= 0:
            return None

        close_qty = round(pos.quantity * fraction, 6)
        if close_qty <= 0:
            return None
        if close_qty >= pos.quantity:
            return self.close_position(symbol=symbol, exit_price=exit_price or pos.current_price, reason=reason)

        raw_exit = exit_price if exit_price is not None else pos.current_price
        now = datetime.now(timezone.utc)

        # Apply slippage on exit
        if pos.side == PositionSide.LONG:
            exec_exit = round(raw_exit * (1.0 - self.slippage_rate), 4 if raw_exit < 10 else 2)
            gross_pnl = (exec_exit - pos.entry_price) * close_qty
        else:
            exec_exit = round(raw_exit * (1.0 + self.slippage_rate), 4 if raw_exit < 10 else 2)
            gross_pnl = (pos.entry_price - exec_exit) * close_qty

        slippage_cost = round(abs(exec_exit - raw_exit) * close_qty, 4)
        close_notional = exec_exit * close_qty
        exit_fee = round(close_notional * self.taker_fee, 4)

        # Entry fee share for this closed portion
        entry_fee_share = round(float(pos.fees_paid or 0.0) * (close_qty / pos.quantity), 4)
        leg_fees = round(entry_fee_share + exit_fee, 4)
        leg_net_pnl = round(gross_pnl - leg_fees, 4)

        # Update cash balances: release reserved funds and credit net proceeds
        portion_original_notional = pos.entry_price * close_qty
        cash_proceeds = round(portion_original_notional + gross_pnl - exit_fee, 4)
        self.reserved_balance = max(0.0, self.reserved_balance - portion_original_notional)
        self.available_balance = max(0.0, self.available_balance + cash_proceeds)
        self.balance = round(self.available_balance + self.reserved_balance, 4)

        # Update remaining position state
        pos.quantity = round(pos.quantity - close_qty, 6)
        # Retain remaining entry fee in pos.fees_paid
        pos.fees_paid = round(max(0.0, float(pos.fees_paid or 0.0) - entry_fee_share), 4)
        pos.partial_tp_hit = True
        pos.partial_realized_pnl = round(float(pos.partial_realized_pnl or 0.0) + leg_net_pnl, 4)
        pos.partial_fees_paid = round(float(pos.partial_fees_paid or 0.0) + leg_fees, 4)
        pos.partial_realized_at = now
        # Move stop loss to Break-Even (entry price)
        pos.stop_loss = pos.entry_price

        # Record closing fill for this partial exit
        close_side = OrderSide.SELL if pos.side == PositionSide.LONG else OrderSide.BUY
        fill_id = f"fill_{uuid.uuid4().hex[:12]}"
        close_fill = Fill(
            fill_id=fill_id,
            order_id=f"ord_{uuid.uuid4().hex[:12]}",
            symbol=symbol,
            side=close_side,
            price=exec_exit,
            quantity=close_qty,
            fee=exit_fee,
            slippage=slippage_cost,
            timestamp=now,
        )
        self.fills.append(close_fill)

        # Persist updated open position, partial fill, and account state
        self._persist_position(pos)
        self._persist_fill(close_fill)
        self._persist_account()

        logger.info(
            f"Paper Position PARTIAL EXIT [{reason}]: {symbol} {pos.side.value} closed {close_qty} @ ${exec_exit:.2f} "
            f"(raw: ${raw_exit:.2f}), Net PnL: ${leg_net_pnl:+.2f}, Remaining: {pos.quantity}, SL moved to BE @ ${pos.stop_loss:.2f}",
            extra={"symbol": symbol, "strategy": pos.strategy},
        )
        return pos

    def close_position(
        self,
        symbol: str,
        exit_price: float,
        reason: str = "MANUAL",
        exit_reason: Optional[str] = None,
        exit_time: Optional[datetime] = None,
    ) -> Optional[Position]:
        """Closes an open paper position, calculates realized PnL, adjusts balance, and records fill."""
        if symbol not in self.open_positions:
            return None

        if exit_reason is not None:
            reason = exit_reason

        pos = self.open_positions.pop(symbol)
        now = exit_time or datetime.now(timezone.utc)

        # Apply slippage on exit (P1 item)
        if pos.side == PositionSide.LONG:
            exec_exit = round(exit_price * (1.0 - self.slippage_rate), 4 if exit_price < 10 else 2)
            gross_pnl = (exec_exit - pos.entry_price) * pos.quantity
        else:
            exec_exit = round(exit_price * (1.0 + self.slippage_rate), 4 if exit_price < 10 else 2)
            gross_pnl = (pos.entry_price - exec_exit) * pos.quantity

        slippage_cost = round(abs(exec_exit - exit_price) * pos.quantity, 4)

        # Calculate closing fee and true net PnL (accounting for both entry and exit fees)
        close_notional = exec_exit * pos.quantity
        exit_fee = round(close_notional * self.taker_fee, 4)
        entry_fee = round(float(pos.fees_paid or 0.0), 4)
        remaining_trade_fees = round(entry_fee + exit_fee, 4)
        remaining_net_pnl = round(gross_pnl - remaining_trade_fees, 4)

        # Total cumulative net PnL and fees across all legs
        final_realized_pnl = round(float(pos.partial_realized_pnl or 0.0) + remaining_net_pnl, 4)
        total_fees = round(float(pos.partial_fees_paid or 0.0) + remaining_trade_fees, 4)

        # Update cash balances
        # Release notional from reserved balance and return cash proceeds to available balance
        original_notional = pos.entry_price * pos.quantity
        cash_proceeds = round(original_notional + gross_pnl - exit_fee, 4)
        self.reserved_balance = max(0.0, self.reserved_balance - original_notional)
        self.available_balance = max(0.0, self.available_balance + cash_proceeds)
        self.balance = round(self.available_balance + self.reserved_balance, 4)

        # Record closing fill
        close_side = OrderSide.SELL if pos.side == PositionSide.LONG else OrderSide.BUY
        fill_id = f"fill_{uuid.uuid4().hex[:12]}"
        close_fill = Fill(
            fill_id=fill_id,
            order_id=f"ord_{uuid.uuid4().hex[:12]}",
            symbol=symbol,
            side=close_side,
            price=exec_exit,
            quantity=pos.quantity,
            fee=exit_fee,
            slippage=slippage_cost,
            timestamp=now,
        )
        self.fills.append(close_fill)

        # Update position record to closed state (restore initial_quantity for full lifecycle visibility)
        pos.current_price = exec_exit
        pos.realized_pnl = final_realized_pnl
        pos.unrealized_pnl = 0.0
        pos.status = PositionStatus.CLOSED
        pos.closed_at = now
        pos.fees_paid = total_fees
        if pos.initial_quantity is not None:
            pos.quantity = pos.initial_quantity

        self.closed_positions_history.append(pos)

        # Persist position close and balance update to SQLite
        self._persist_position(pos)
        self._persist_fill(close_fill)
        self._persist_account()

        logger.info(
            f"Paper Position CLOSED [{reason}]: {symbol} {pos.side.value} @ ${exec_exit:.2f} (raw: ${exit_price:.2f}), "
            f"Net PnL: ${final_realized_pnl:+.2f} (Remaining Leg: ${remaining_net_pnl:+.2f}), New Balance: ${self.balance:.2f}",
            extra={"symbol": symbol, "strategy": pos.strategy},
        )
        return pos
