"""
Production-Hardened Live Binance Execution Engine.
Strictly implements:
- Real CCXT authenticated Binance Spot order placement (POST /api/v3/order).
- Mandatory Two-Step Live Activation (LIVE_TRADING and LIVE_TRADING_ARMED).
- Exchange-Side Protective Stop submission (STOP_LOSS_LIMIT) immediately following entry fill.
- Fail-Closed invariant: if protective stop fails on exchange, emergency-liquidates position.
- Order query and cancellation (GET / DELETE /api/v3/order).
- Account and open orders retrieval for state reconciliation.
"""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import ccxt.async_support as ccxt

from services.execution.execution_interface import ExecutionEngine
from services.execution.symbol_filters import symbol_filter_engine
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
from shared.schemas import Fill, Order, Position, RiskDecision

logger = get_logger("live-binance-execution", service="execution")
settings = get_settings()


class BinanceLiveExecutionEngine(ExecutionEngine):
    """
    Authoritative Live Binance Spot Execution Engine.
    Requires:
    1. settings.LIVE_TRADING is True
    2. settings.LIVE_TRADING_ARMED is True
    3. Valid Binance API Key and Secret
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        armed: Optional[bool] = None,
        client: Optional[Any] = None,
        db_path: Optional[str] = None,
    ):
        self.api_key = api_key or settings.BINANCE_API_KEY
        self.api_secret = api_secret or settings.BINANCE_API_SECRET
        self.is_armed = armed if armed is not None else getattr(settings, "LIVE_TRADING_ARMED", False)
        self.db_path = db_path or str(getattr(settings, "DATABASE_PATH", None) or "kripto_agent.db")

        # 1. Enforce Two-Step Activation Guardrails
        if not settings.LIVE_TRADING:
            logger.info("BinanceLiveExecutionEngine initialized in SAFE/DISABLED mode.")
            raise RuntimeError("LIVE TRADING IS LOCKED: settings.LIVE_TRADING is False.")

        if not self.is_armed:
            logger.critical("SECURITY INTERLOCK: LIVE_TRADING is True but LIVE_TRADING_ARMED is False.")
            raise RuntimeError("SECURITY VIOLATION: LIVE TRADING IS DISARMED. Two-step arming required.")

        # 2. Enforce Credentials
        if not self.api_key or not self.api_secret:
            logger.critical("LIVE TRADING ABORTED: Missing required Binance API credentials.")
            raise ValueError("LIVE TRADING ERROR: Missing Binance API key or secret.")

        # 3. Instantiate CCXT Client
        self.client = client or ccxt.binance({
            "apiKey": self.api_key,
            "secret": self.api_secret,
            "enableRateLimit": True,
            "timeout": 5000,
            "options": {
                "defaultType": "spot",
                "adjustForTimeDifference": True,
            },
        })

        self.orders: Dict[str, Order] = {}
        self.open_positions: Dict[str, Position] = {}
        self.closed_positions_history: List[Position] = []

        self._init_db()
        self.restore_state()
        logger.warning("🚨 LIVE TRADING ENABLED AND ARMED: Authenticated Binance Spot Execution Active.")

    def _get_db_conn(self):
        if not self.db_path:
            return None
        try:
            conn = sqlite3.connect(self.db_path, timeout=10.0)
            conn.execute("PRAGMA journal_mode=WAL;")
            return conn
        except Exception as e:
            logger.error(f"Live engine SQLite connection failed: {e}")
            return None

    def _init_db(self):
        if not self.db_path:
            return
        conn = self._get_db_conn()
        if not conn:
            return
        try:
            with conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS live_positions (
                        position_id TEXT PRIMARY KEY,
                        symbol TEXT NOT NULL,
                        side TEXT NOT NULL,
                        entry_price REAL NOT NULL,
                        quantity REAL NOT NULL,
                        current_price REAL NOT NULL,
                        stop_loss REAL NOT NULL,
                        take_profit REAL NOT NULL,
                        unrealized_pnl REAL DEFAULT 0.0,
                        realized_pnl REAL DEFAULT 0.0,
                        status TEXT NOT NULL,
                        opened_at TEXT NOT NULL,
                        closed_at TEXT,
                        fees_paid REAL DEFAULT 0.0,
                        strategy TEXT,
                        stop_order_id TEXT,
                        last_price_update_at TEXT,
                        price_fetch_failures INTEGER DEFAULT 0,
                        price_stale INTEGER DEFAULT 0,
                        metadata TEXT
                    );
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS live_orders (
                        order_id TEXT PRIMARY KEY,
                        symbol TEXT NOT NULL,
                        order_type TEXT NOT NULL,
                        side TEXT NOT NULL,
                        quantity REAL NOT NULL,
                        price REAL,
                        status TEXT NOT NULL,
                        filled_quantity REAL DEFAULT 0.0,
                        average_fill_price REAL DEFAULT 0.0,
                        fee_paid REAL DEFAULT 0.0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                """)
        except Exception as e:
            logger.error(f"Live engine database schema init error: {e}")
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
            last_price_iso = pos.last_price_update_at.isoformat() if (getattr(pos, "last_price_update_at", None) and hasattr(pos.last_price_update_at, "isoformat")) else (str(pos.last_price_update_at) if getattr(pos, "last_price_update_at", None) else None)
            meta_json = json.dumps(getattr(pos, "metadata", {}) or {})
            with conn:
                conn.execute("""
                    INSERT INTO live_positions (
                        position_id, symbol, side, entry_price, quantity, current_price,
                        stop_loss, take_profit, unrealized_pnl, realized_pnl, status,
                        opened_at, closed_at, fees_paid, strategy, stop_order_id,
                        last_price_update_at, price_fetch_failures, price_stale, metadata
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        stop_order_id=COALESCE(excluded.stop_order_id, live_positions.stop_order_id),
                        last_price_update_at=excluded.last_price_update_at,
                        price_fetch_failures=excluded.price_fetch_failures,
                        price_stale=excluded.price_stale,
                        metadata=excluded.metadata
                """, (
                    pos.position_id, pos.symbol, side_str, pos.entry_price, pos.quantity, pos.current_price,
                    pos.stop_loss, pos.take_profit, pos.unrealized_pnl, pos.realized_pnl, status_str,
                    opened_iso, closed_iso, pos.fees_paid, pos.strategy, getattr(pos, "stop_order_id", None),
                    last_price_iso, getattr(pos, "price_fetch_failures", 0), 1 if getattr(pos, "price_stale", False) else 0,
                    meta_json,
                ))
        except Exception as e:
            logger.error(f"Failed to persist live position {pos.position_id}: {e}")
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
                    INSERT INTO live_orders (order_id, symbol, order_type, side, quantity, price, status, filled_quantity, average_fill_price, fee_paid, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(order_id) DO UPDATE SET
                        status=excluded.status,
                        filled_quantity=excluded.filled_quantity,
                        average_fill_price=excluded.average_fill_price,
                        fee_paid=excluded.fee_paid,
                        updated_at=excluded.updated_at
                """, (order.order_id, order.symbol, type_str, side_str, order.quantity, order.price, status_str, order.filled_quantity, order.average_fill_price, order.fee_paid, created_iso, updated_iso))
        except Exception as e:
            logger.error(f"Failed to persist live order {order.order_id}: {e}")
        finally:
            conn.close()

    def restore_state(self):
        if not self.db_path:
            return
        conn = self._get_db_conn()
        if not conn:
            return
        try:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM live_positions WHERE status = 'OPEN'")
            for row in cursor.fetchall():
                try:
                    meta = json.loads(row["metadata"]) if row["metadata"] else {}
                except Exception:
                    meta = {}
                p = Position(
                    position_id=row["position_id"],
                    symbol=row["symbol"],
                    side=PositionSide(row["side"]),
                    entry_price=row["entry_price"],
                    quantity=row["quantity"],
                    current_price=row["current_price"],
                    stop_loss=row["stop_loss"],
                    take_profit=row["take_profit"],
                    unrealized_pnl=row["unrealized_pnl"],
                    realized_pnl=row["realized_pnl"],
                    status=PositionStatus(row["status"]),
                    opened_at=datetime.fromisoformat(row["opened_at"]),
                    fees_paid=row["fees_paid"],
                    strategy=row["strategy"] or "",
                    stop_order_id=row["stop_order_id"],
                    last_price_update_at=datetime.fromisoformat(row["last_price_update_at"]) if row["last_price_update_at"] else None,
                    price_fetch_failures=row["price_fetch_failures"] or 0,
                    price_stale=bool(row["price_stale"]),
                    metadata=meta,
                )
                self.open_positions[p.symbol] = p

            cursor.execute("SELECT * FROM live_positions WHERE status = 'CLOSED' ORDER BY closed_at ASC")
            for row in cursor.fetchall():
                try:
                    meta = json.loads(row["metadata"]) if row["metadata"] else {}
                except Exception:
                    meta = {}
                p = Position(
                    position_id=row["position_id"],
                    symbol=row["symbol"],
                    side=PositionSide(row["side"]),
                    entry_price=row["entry_price"],
                    quantity=row["quantity"],
                    current_price=row["current_price"],
                    stop_loss=row["stop_loss"],
                    take_profit=row["take_profit"],
                    unrealized_pnl=row["unrealized_pnl"],
                    realized_pnl=row["realized_pnl"],
                    status=PositionStatus(row["status"]),
                    opened_at=datetime.fromisoformat(row["opened_at"]),
                    closed_at=datetime.fromisoformat(row["closed_at"]) if row["closed_at"] else None,
                    fees_paid=row["fees_paid"],
                    strategy=row["strategy"] or "",
                    stop_order_id=row["stop_order_id"],
                    metadata=meta,
                )
                self.closed_positions_history.append(p)

            logger.info(f"Live engine state restored from SQLite: {len(self.open_positions)} open positions, {len(self.closed_positions_history)} closed positions.")
        except Exception as e:
            logger.error(f"Failed to restore live engine state from SQLite: {e}")
        finally:
            conn.close()

    def _verify_safety_gates(self):
        """Hard assertion on runtime execution authorization."""
        if not settings.LIVE_TRADING or not self.is_armed:
            raise RuntimeError("SECURITY VIOLATION: Live trading execution gate is not armed.")

    async def submit_order(
        self, decision: RiskDecision, strategy_name: str = ""
    ) -> Tuple[Order, Fill, Position]:
        """
        Executes real market order on Binance Spot and places exchange-side protective stop.
        """
        self._verify_safety_gates()

        if not decision or not decision.approved:
            raise PermissionError(f"Rejected unapproved decision: {getattr(decision, 'reason', '')}")

        # Spot Mode Enforcement
        if decision.direction == SignalDirection.SHORT:
            raise ValueError(f"Spot mode does not support SHORT execution on {decision.symbol}. (SIGNAL_ONLY)")

        # Normalize with exchange symbol filters
        is_valid, reject_reason, norm_price, norm_qty = symbol_filter_engine.normalize_and_validate(
            symbol=decision.symbol,
            price=decision.entry_price,
            quantity=decision.calculated_size,
        )
        if not is_valid:
            raise ValueError(f"SYMBOL_FILTER_REJECTION: {reject_reason}")

        client_order_id = f"ko_{uuid.uuid4().hex[:16]}"
        now = datetime.now(timezone.utc)

        # 1. Submit Market Entry Order to Binance
        try:
            logger.info(
                f"Submitting LIVE Binance BUY order for {decision.symbol}: qty={norm_qty}, clientOrderId={client_order_id}"
            )
            raw_res = await self.client.create_order(
                symbol=decision.symbol,
                type="market",
                side="buy",
                amount=norm_qty,
                params={"newClientOrderId": client_order_id},
            )
        except Exception as e:
            logger.error(f"Live Binance order submission failed for {decision.symbol}: {e}")
            raise

        raw_status = str(raw_res.get("status") or raw_res.get("info", {}).get("status") or "").upper()
        if raw_status in {"REJECTED", "CANCELED", "EXPIRED"}:
            raise RuntimeError(f"Live Binance order was {raw_status}: {raw_res}")

        binance_order_id = str(raw_res.get("id"))
        fill_price = float(raw_res.get("average") or raw_res.get("price") or norm_price)
        filled_qty = float(raw_res.get("filled") or raw_res.get("executedQty") or (norm_qty if raw_status in {"FILLED", "CLOSED"} else 0.0))
        fee_cost = float(raw_res.get("fee", {}).get("cost", 0.0)) if raw_res.get("fee") else 0.0

        if filled_qty <= 0:
            raise RuntimeError(f"Live Binance order produced zero filled quantity (status={raw_status})")

        order_status = OrderStatus.FILLED if (filled_qty >= norm_qty or raw_status in {"FILLED", "CLOSED"}) else OrderStatus.PARTIALLY_FILLED

        order = Order(
            order_id=binance_order_id,
            symbol=decision.symbol,
            order_type=OrderType.MARKET,
            side=OrderSide.BUY,
            quantity=norm_qty,
            price=fill_price,
            status=order_status,
            filled_quantity=filled_qty,
            average_fill_price=fill_price,
            fee_paid=fee_cost,
            created_at=now,
            updated_at=now,
        )
        self.orders[binance_order_id] = order

        fill = Fill(
            fill_id=f"fill_{binance_order_id}",
            order_id=binance_order_id,
            symbol=decision.symbol,
            side=OrderSide.BUY,
            price=fill_price,
            quantity=filled_qty,
            fee=fee_cost,
            slippage=0.0,
            timestamp=now,
        )

        position_id = f"pos_{binance_order_id}"
        position = Position(
            position_id=position_id,
            symbol=decision.symbol,
            side=PositionSide.LONG,
            entry_price=fill_price,
            current_price=fill_price,
            quantity=filled_qty,
            stop_loss=decision.stop_loss,
            take_profit=decision.take_profit,
            unrealized_pnl=0.0,
            realized_pnl=0.0,
            status=PositionStatus.OPEN,
            strategy=strategy_name,
            opened_at=now,
            peak_price=fill_price,
            fees_paid=fee_cost,
        )

        # 2. INVARIANT P0-RISK-002: Submit Exchange-Side Protective Stop
        stop_client_id = f"ks_{uuid.uuid4().hex[:16]}"
        spec = symbol_filter_engine.filters.get(decision.symbol, {})
        tick_size = spec.get("tick_size", 0.01 if decision.stop_loss >= 1.0 else 0.0001)
        step_size = spec.get("step_size", 0.0001)

        norm_stop_price = symbol_filter_engine.round_to_tick_size(decision.stop_loss, tick_size)
        norm_limit_price = symbol_filter_engine.round_to_tick_size(decision.stop_loss * 0.999, tick_size)
        norm_stop_qty = symbol_filter_engine.round_to_step_size(filled_qty, step_size)

        try:
            logger.info(
                f"Submitting LIVE Exchange-Side Protective Stop on Binance: {decision.symbol} "
                f"STOP_LOSS_LIMIT @ ${norm_stop_price} (limit: ${norm_limit_price}, qty: {norm_stop_qty})"
            )
            # Binance Spot STOP_LOSS_LIMIT order: stopPrice triggers order at limit price
            stop_res = await self.client.create_order(
                symbol=decision.symbol,
                type="STOP_LOSS_LIMIT",
                side="sell",
                amount=norm_stop_qty,
                price=norm_limit_price,
                params={
                    "stopPrice": norm_stop_price,
                    "newClientOrderId": stop_client_id,
                    "timeInForce": "GTC",
                },
            )
            stop_order_id = str(stop_res.get("id") or "")
            stop_status = str(stop_res.get("status") or stop_res.get("info", {}).get("status") or "").upper()
            if not stop_order_id or stop_status in {"REJECTED", "CANCELED", "EXPIRED"}:
                raise RuntimeError(f"Protective stop rejected by Binance: id={stop_order_id}, status={stop_status}")
            position.stop_order_id = stop_order_id
            logger.info(f"Exchange-Side Protective Stop ACCEPTED by Binance: ID={stop_order_id}, status={stop_status}")
        except Exception as stop_err:
            logger.critical(
                f"FATAL SECURITY VIOLATION: Protective stop failed on Binance ({stop_err}). Emergency liquidating position to fail closed!"
            )
            emergency_liq_success = False
            try:
                await self.client.create_order(
                    symbol=decision.symbol,
                    type="market",
                    side="sell",
                    amount=norm_stop_qty,
                )
                emergency_liq_success = True
                logger.warning(f"Emergency liquidation SUCCEEDED for {decision.symbol} after protective stop failure.")
            except Exception as close_err:
                logger.critical(f"FATAL ALERT: Emergency liquidation also failed for {decision.symbol}: {close_err}")
                position.status = PositionStatus.OPEN
                position.metadata["emergency_unprotected"] = True
                position.metadata["stop_error"] = str(stop_err)
                position.metadata["liquidation_error"] = str(close_err)
                self.open_positions[decision.symbol] = position
                self._persist_order(order)
                self._persist_position(position)
                raise RuntimeError(
                    f"FATAL: Protective stop failed ({stop_err}) AND emergency liquidation failed ({close_err}). "
                    f"Position {decision.symbol} is OPEN and UNPROTECTED on Binance! Retained in local tracker."
                )

            if emergency_liq_success:
                position.status = PositionStatus.CLOSED
                position.closed_at = datetime.now(timezone.utc)
                self.closed_positions_history.append(position)
                self._persist_order(order)
                self._persist_position(position)
                raise RuntimeError(
                    f"FAIL-CLOSED INVARIANT: Protective stop rejected by Binance ({stop_err}). "
                    f"Position {decision.symbol} was emergency-liquidated on exchange."
                )

        self.open_positions[decision.symbol] = position
        self._persist_order(order)
        self._persist_position(position)
        return order, fill, position

    async def cancel_order(self, order_id: str, symbol: Optional[str] = None) -> bool:
        """Cancels order on Binance exchange."""
        self._verify_safety_gates()
        try:
            res = await self.client.cancel_order(order_id, symbol=symbol)
            if order_id in self.orders:
                self.orders[order_id].status = OrderStatus.CANCELED
                self._persist_order(self.orders[order_id])
            return res.get("status") in ["CANCELED", "canceled", True]
        except Exception as e:
            logger.error(f"Failed to cancel Binance order {order_id}: {e}")
            return False

    async def cancel_all_orders(self, symbol: Optional[str] = None) -> int:
        """Cancels all open orders on Binance exchange."""
        self._verify_safety_gates()
        try:
            open_orders = await self.client.fetch_open_orders(symbol=symbol)
            count = 0
            for o in open_orders:
                oid = str(o["id"])
                sym = o.get("symbol")
                if await self.cancel_order(oid, symbol=sym):
                    count += 1
            return count
        except Exception as e:
            logger.error(f"Failed to cancel all open orders on Binance: {e}")
            return 0

    async def get_order(self, order_id: str, symbol: Optional[str] = None) -> Optional[Order]:
        """Queries authoritative order status from Binance."""
        self._verify_safety_gates()
        try:
            raw = await self.client.fetch_order(order_id, symbol=symbol)
            now = datetime.now(timezone.utc)
            order = Order(
                order_id=str(raw["id"]),
                symbol=raw.get("symbol", ""),
                order_type=OrderType.MARKET,
                side=OrderSide.BUY if raw.get("side") == "buy" else OrderSide.SELL,
                quantity=float(raw.get("amount", 0.0)),
                price=float(raw.get("price", 0.0)),
                status=OrderStatus.FILLED if raw.get("status") == "closed" else OrderStatus.SUBMITTED,
                filled_quantity=float(raw.get("filled", 0.0)),
                average_fill_price=float(raw.get("average") or raw.get("price", 0.0)),
                fee_paid=float(raw.get("fee", {}).get("cost", 0.0)) if raw.get("fee") else 0.0,
                created_at=now,
                updated_at=now,
            )
            self._persist_order(order)
            return order
        except Exception as e:
            logger.error(f"Failed to fetch Binance order {order_id}: {e}")
            return None

    async def fetch_open_orders(self, symbol: Optional[str] = None) -> List[Dict[str, Any]]:
        """Queries open orders for reconciliation."""
        self._verify_safety_gates()
        return await self.client.fetch_open_orders(symbol=symbol)

    async def fetch_balance(self) -> Dict[str, Any]:
        """Queries live account balances for reconciliation."""
        self._verify_safety_gates()
        return await self.client.fetch_balance()

    async def close(self):
        """Clean shutdown of CCXT client session."""
        if hasattr(self.client, "close"):
            await self.client.close()

    def update_market_price(self, symbol: str, price: float) -> Optional[Position]:
        """Updates market price and unrealized PnL of open live position for autonomous runner compatibility."""
        pos = self.open_positions.get(symbol)
        if not pos:
            return None
        pos.current_price = price
        pos.last_price_update_at = datetime.now(timezone.utc)
        pos.price_fetch_failures = 0
        pos.price_stale = False
        if pos.entry_price and pos.entry_price > 0:
            if pos.side == PositionSide.LONG:
                pos.unrealized_pnl = (price - pos.entry_price) * pos.quantity
            else:
                pos.unrealized_pnl = (pos.entry_price - price) * pos.quantity
        self._persist_position(pos)
        return pos

    def record_price_fetch_failure(self, symbol: str, error_msg: str) -> None:
        """Records a price fetch failure and marks position stale if threshold reached."""
        pos = self.open_positions.get(symbol)
        if pos:
            pos.price_fetch_failures += 1
            if pos.price_fetch_failures >= 5:
                pos.price_stale = True
            self._persist_position(pos)

    async def close_position(
        self,
        symbol: str = "",
        exit_price: Optional[float] = None,
        reason: str = "MANUAL_CLOSE",
        position_id: Optional[str] = None,
        current_price: Optional[float] = None,
    ) -> Optional[Position]:
        """Closes an open position on Binance via market order, cancels protective stop, and records PnL."""
        self._verify_safety_gates()
        target_symbol = None
        target_pos = None
        lookup_id = symbol or position_id or ""
        exec_price_hint = exit_price if exit_price is not None else current_price

        for sym, pos in list(self.open_positions.items()):
            if (
                pos.position_id == lookup_id
                or sym == lookup_id
                or sym.replace("/", "") == lookup_id.replace("/", "")
            ):
                target_symbol = sym
                target_pos = pos
                break

        if not target_pos or not target_symbol:
            logger.warning(f"Live close_position: Position {lookup_id} not found in open positions.")
            return None

        # 1. Cancel exchange-side protective stop if order_id exists
        if target_pos.stop_order_id:
            try:
                await self.client.cancel_order(target_pos.stop_order_id, symbol=target_symbol)
                logger.info(f"Canceled protective stop {target_pos.stop_order_id} for {target_symbol}")
            except Exception as e:
                logger.warning(f"Could not cancel protective stop {target_pos.stop_order_id}: {e}")

        # 2. Submit market order to exit on Binance Spot
        spec = symbol_filter_engine.get_filter_spec(target_symbol)
        norm_qty = symbol_filter_engine.round_to_step_size(target_pos.quantity, spec["step_size"])
        try:
            exit_order = await self.client.create_order(
                symbol=target_symbol,
                type="market",
                side="sell" if target_pos.side == PositionSide.LONG else "buy",
                amount=norm_qty,
            )
            exec_price = float(exit_order.get("price") or exit_order.get("average") or exec_price_hint or target_pos.current_price)
        except Exception as ex:
            logger.critical(f"FATAL: Failed to execute live exit order on Binance for {target_symbol}: {ex}")
            raise RuntimeError(f"Live exit failed on Binance: {ex}")

        # 3. Finalize position accounting
        target_pos.status = PositionStatus.CLOSED
        target_pos.closed_at = datetime.now(timezone.utc)
        target_pos.close_reason = reason
        if target_pos.side == PositionSide.LONG:
            target_pos.realized_pnl = (exec_price - target_pos.entry_price) * norm_qty - target_pos.fees_paid
        else:
            target_pos.realized_pnl = (target_pos.entry_price - exec_price) * norm_qty - target_pos.fees_paid

        del self.open_positions[target_symbol]
        self.closed_positions_history.append(target_pos)
        self._persist_position(target_pos)
        logger.info(f"Live position {target_symbol} CLOSED at ${exec_price:.4f} (PnL: ${target_pos.realized_pnl:.2f}, Reason: {reason})")
        return target_pos

    async def emergency_close_all(self) -> int:
        """Cancels all orders and emergency closes all open positions on Binance."""
        self._verify_safety_gates()
        await self.cancel_all_orders()
        closed_count = 0
        for sym in list(self.open_positions.keys()):
            try:
                pos = self.open_positions[sym]
                await self.close_position(pos.position_id, reason="EMERGENCY_CLOSE_ALL")
                closed_count += 1
            except Exception as e:
                logger.critical(f"Emergency close failed for {sym}: {e}")
        return closed_count

    @property
    def balance(self) -> float:
        return getattr(self, "_cached_balance", 0.0)

    @property
    def equity(self) -> float:
        unrealized = sum(p.unrealized_pnl for p in self.open_positions.values())
        return self.balance + unrealized

    @property
    def is_spot_mode(self) -> bool:
        return True

    async def sync_account_balance(self) -> float:
        """Queries Binance for current USDT spot balance."""
        try:
            bal_res = await self.client.fetch_balance()
            usdt_bal = float(bal_res.get("USDT", {}).get("free", 0.0))
            self._cached_balance = usdt_bal
            return usdt_bal
        except Exception as e:
            logger.warning(f"Could not fetch Binance balance: {e}")
            return getattr(self, "_cached_balance", 0.0)

