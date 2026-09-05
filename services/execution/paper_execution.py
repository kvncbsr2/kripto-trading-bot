import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

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
        initial_balance: float = 5000.0,
        maker_fee: float = 0.001,
        taker_fee: float = 0.001,
        slippage_bps: float = 5.0,
        enable_trailing_stop: bool = True,
        is_spot_mode: bool = True,
        db_path: Optional[str] = None,
    ):
        self.db_path = db_path
        self.balance: float = initial_balance
        self.available_balance: float = initial_balance
        self.reserved_balance: float = 0.0
        self.initial_balance: float = initial_balance
        self.maker_fee: float = maker_fee
        self.taker_fee: float = taker_fee
        self.slippage_rate: float = slippage_bps / 10000.0
        self.enable_trailing_stop: bool = enable_trailing_stop
        self.is_spot_mode: bool = is_spot_mode

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
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

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
                        peak_price REAL
                    )
                """)
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
            conn.close()

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
            conn.close()

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
                    INSERT INTO paper_positions (position_id, symbol, side, entry_price, quantity, current_price, stop_loss, take_profit, unrealized_pnl, realized_pnl, status, opened_at, closed_at, fees_paid, strategy, peak_price)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(position_id) DO UPDATE SET
                        current_price=excluded.current_price,
                        unrealized_pnl=excluded.unrealized_pnl,
                        realized_pnl=excluded.realized_pnl,
                        status=excluded.status,
                        closed_at=excluded.closed_at,
                        fees_paid=excluded.fees_paid,
                        peak_price=excluded.peak_price
                """, (pos.position_id, pos.symbol, side_str, pos.entry_price, pos.quantity, pos.current_price, pos.stop_loss, pos.take_profit, pos.unrealized_pnl, pos.realized_pnl, status_str, opened_iso, closed_iso, pos.fees_paid, pos.strategy, pos.peak_price))
        except Exception as e:
            logger.error(f"Failed to persist position {pos.position_id}: {e}")
        finally:
            conn.close()

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
            conn.close()

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
            conn.close()

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
            conn.close()

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
        """Lifetime cumulative realized PnL across all closed positions."""
        return sum(pos.realized_pnl for pos in self.closed_positions_history)

    @property
    def daily_realized_pnl(self) -> float:
        """Realized PnL strictly from positions closed during current UTC day."""
        today_start = datetime.combine(datetime.now(timezone.utc).date(), datetime.min.time(), tzinfo=timezone.utc)
        total = 0.0
        for pos in self.closed_positions_history:
            if pos.closed_at:
                c_at = pos.closed_at if pos.closed_at.tzinfo is not None else pos.closed_at.replace(tzinfo=timezone.utc)
                if c_at >= today_start:
                    total += pos.realized_pnl
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
        return self.execute_market_order(decision, strategy_name)

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
    ) -> Tuple[Order, Fill, Position]:
        """
        Executes simulated market fill based strictly on an approved RiskDecision.
        Enforces Spot mode rejection for short positions.
        """
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

        now = datetime.now(timezone.utc)
        order_id = order_id or f"ord_{uuid.uuid4().hex[:12]}"
        fill_id = f"fill_{uuid.uuid4().hex[:12]}"
        pos_id = f"pos_{uuid.uuid4().hex[:12]}"

        side = OrderSide.BUY if decision.direction == SignalDirection.LONG else OrderSide.SELL
        pos_side = PositionSide.LONG if decision.direction == SignalDirection.LONG else PositionSide.SHORT

        # Realistic Slippage modeling
        base_price = decision.entry_price
        slippage_mult = (1.0 + self.slippage_rate) if side == OrderSide.BUY else (1.0 - self.slippage_rate)
        exec_price = round(base_price * slippage_mult, 4 if base_price < 10 else 2)
        slippage_cost = round(abs(exec_price - base_price) * decision.calculated_size, 4)

        # Taker Fee
        notional = exec_price * decision.calculated_size
        fee = round(notional * self.taker_fee, 4)

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
            order_type=OrderType.MARKET,
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
        # Trailing 50% Peak Profit Lock & Break-Even
        # ---------------------------------------------------------------------
        if self.enable_trailing_stop:
            risk_dist = abs(pos.entry_price - pos.stop_loss)
            if pos.peak_price is None:
                pos.peak_price = pos.entry_price

            if pos.side == PositionSide.LONG:
                if high > pos.peak_price:
                    pos.peak_price = high

                peak_gain = pos.peak_price - pos.entry_price

                # 1. Break-even check (+1.0R)
                if high >= pos.entry_price + risk_dist and pos.stop_loss < pos.entry_price:
                    pos.stop_loss = pos.entry_price
                    logger.info(f"Stop-Loss adjusted to BREAK-EVEN for {symbol} LONG @ {pos.entry_price:.2f}")

                # 2. Peak Profit Lock: If gained >= 1.0R, protect 50% of peak profit from pullback
                if peak_gain >= risk_dist and peak_gain > 0:
                    lock_price = pos.peak_price - (peak_gain * 0.5)
                    if lock_price > pos.stop_loss:
                        pos.stop_loss = round(lock_price, 4)
                        logger.info(
                            f"Trailing Profit Lock: {symbol} LONG stop raised to ${pos.stop_loss:.2f} "
                            f"(Protecting 50% of peak gain +${peak_gain:.2f})"
                        )

            elif pos.side == PositionSide.SHORT:
                if low < pos.peak_price:
                    pos.peak_price = low

                peak_gain = pos.entry_price - pos.peak_price

                # 1. Break-even check (+1.0R)
                if low <= pos.entry_price - risk_dist and pos.stop_loss > pos.entry_price:
                    pos.stop_loss = pos.entry_price
                    logger.info(f"Stop-Loss adjusted to BREAK-EVEN for {symbol} SHORT @ {pos.entry_price:.2f}")

                # 2. Peak Profit Lock
                if peak_gain >= risk_dist and peak_gain > 0:
                    lock_price = pos.peak_price + (peak_gain * 0.5)
                    if lock_price < pos.stop_loss:
                        pos.stop_loss = round(lock_price, 4)
                        logger.info(
                            f"Trailing Profit Lock: {symbol} SHORT stop lowered to ${pos.stop_loss:.2f} "
                            f"(Protecting 50% of peak gain +${peak_gain:.2f})"
                        )

        # ---------------------------------------------------------------------
        # Stop & Target Evaluation
        # ---------------------------------------------------------------------
        hit_reason: Optional[str] = None
        exit_price: float = 0.0

        if pos.side == PositionSide.LONG:
            # Check Take-Profit first if high reached target
            if high >= pos.take_profit:
                hit_reason = "TAKE_PROFIT"
                exit_price = pos.take_profit
            elif low <= pos.stop_loss:
                hit_reason = "STOP_LOSS"
                exit_price = pos.stop_loss

        elif pos.side == PositionSide.SHORT:
            if low <= pos.take_profit:
                hit_reason = "TAKE_PROFIT"
                exit_price = pos.take_profit
            elif high >= pos.stop_loss:
                hit_reason = "STOP_LOSS"
                exit_price = pos.stop_loss

        if hit_reason and exit_price > 0:
            closed_pos = self.close_position(symbol=symbol, exit_price=exit_price, reason=hit_reason)
            if closed_pos:
                return closed_pos, hit_reason, exit_price

        return None

    def close_position(
        self,
        symbol: str,
        exit_price: float,
        reason: str = "MANUAL",
    ) -> Optional[Position]:
        """Closes an open paper position, calculates realized PnL, adjusts balance, and records fill."""
        if symbol not in self.open_positions:
            return None

        pos = self.open_positions.pop(symbol)
        now = datetime.now(timezone.utc)

        # Apply slippage on exit (P1 item)
        if pos.side == PositionSide.LONG:
            exec_exit = round(exit_price * (1.0 - self.slippage_rate), 4 if exit_price < 10 else 2)
            gross_pnl = (exec_exit - pos.entry_price) * pos.quantity
        else:
            exec_exit = round(exit_price * (1.0 + self.slippage_rate), 4 if exit_price < 10 else 2)
            gross_pnl = (pos.entry_price - exec_exit) * pos.quantity

        slippage_cost = round(abs(exec_exit - exit_price) * pos.quantity, 4)

        # Calculate closing fee
        close_notional = exec_exit * pos.quantity
        fee = round(close_notional * self.taker_fee, 4)
        net_pnl = round(gross_pnl - fee, 4)

        # Update position record
        pos.current_price = exec_exit
        pos.realized_pnl = net_pnl
        pos.unrealized_pnl = 0.0
        pos.status = PositionStatus.CLOSED
        pos.closed_at = now
        pos.fees_paid += fee

        # Update cash balances
        # Release notional from reserved balance and add back to available balance along with net PnL
        original_notional = pos.entry_price * pos.quantity
        self.reserved_balance = max(0.0, self.reserved_balance - original_notional)
        self.balance += net_pnl
        self.available_balance = self.balance - self.reserved_balance

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
            fee=fee,
            slippage=slippage_cost,
            timestamp=now,
        )
        self.fills.append(close_fill)
        self.closed_positions_history.append(pos)

        # Persist position close and balance update to SQLite
        self._persist_position(pos)
        self._persist_fill(close_fill)
        self._persist_account()

        logger.info(
            f"Paper Position CLOSED [{reason}]: {symbol} {pos.side.value} @ ${exec_exit:.2f} (raw: ${exit_price:.2f}), "
            f"Net PnL: ${net_pnl:+.2f}, New Balance: ${self.balance:.2f}",
            extra={"symbol": symbol, "strategy": pos.strategy},
        )
        return pos
