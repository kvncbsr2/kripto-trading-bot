import asyncio
import hashlib
import time
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from services.execution.symbol_filters import symbol_filter_engine
from shared.enums import (
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
    PositionStatus,
    SignalDirection,
)
from shared.config import get_settings
from shared.logging import get_logger
from shared.schemas import Fill, Order, Position, RiskDecision

logger = get_logger("order-manager", service="execution")


class OrderManager:
    """
    Centralized Single Order Authority for KRIPTO AGENT (Section 38).
    Responsibilities:
    - Enforces Order State Machine (CREATED -> SUBMITTING -> SUBMITTED -> ACKNOWLEDGED -> FILLED).
    - Idempotency key deduplication (prevents duplicate orders from network retries or multiple agents).
    - Exchange symbol filter normalization (stepSize, tickSize, minNotional).
    - Protective order invariant verification (ensures every live position has a protective stop).
    - Notifies RiskEngine to increment trade count strictly upon fill.
    """

    def __init__(self, execution_engine=None, risk_engine=None):
        self.execution_engine = execution_engine
        self.risk_engine = risk_engine
        self.orders: Dict[str, Order] = {}
        self.idempotency_map: Dict[str, str] = {}  # idempotency_key -> order_id
        self.idempotency_timestamps: Dict[str, datetime] = {}  # idempotency_key -> created_at
        self.protective_stops: Dict[str, Order] = {}  # symbol -> protective stop order

    def bind_execution_engine(self, engine):
        self.execution_engine = engine

    def bind_risk_engine(self, risk_engine):
        self.risk_engine = risk_engine

    @staticmethod
    def generate_idempotency_key(
        strategy: str,
        symbol: str,
        signal_id: str,
        side: str,
        signal_timestamp: Optional[datetime] = None,
        time_bucket_seconds: int = 15,
    ) -> str:
        """Generates deterministic idempotency key anchored to candle/signal timestamp to avoid boundary splits."""
        if signal_timestamp is not None:
            ts = signal_timestamp.timestamp() if hasattr(signal_timestamp, "timestamp") else float(signal_timestamp)
            bucket = int(ts / time_bucket_seconds)
        else:
            bucket = int(time.time() / time_bucket_seconds)
        raw = f"{strategy}:{symbol}:{signal_id}:{side}:{bucket}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    async def execute_risk_decision(
        self,
        decision: RiskDecision,
        strategy_name: str = "",
        signal_id: Optional[str] = None,
        entry_context: Optional[str] = None,
        entry_arm: Optional[str] = None,
    ) -> Tuple[Order, Fill, Position]:
        """
        Main execution pipeline:
        1. Validate RiskDecision approval.
        2. Idempotency check anchored to signal timestamp.
        3. Symbol filter normalization.
        4. State machine progression.
        5. Execution engine submission (async non-blocking).
        6. Protective stop verification & real submission.
        7. Record trade count.
        """
        if self.execution_engine and getattr(self.execution_engine, "is_halted", False) is True:
            raise PermissionError("OrderManager rejection: Execution engine is currently HALTED by emergency stop.")

        if self.risk_engine and hasattr(self.risk_engine, "circuit_breaker"):
            cb_state = getattr(self.risk_engine.circuit_breaker, "state", None)
            if cb_state and getattr(cb_state, "value", str(cb_state)) in ["TRIPPED", "EMERGENCY_HALT"]:
                raise PermissionError(f"OrderManager rejection: Risk circuit breaker is {cb_state}.")

        if not decision or not decision.approved:
            raise PermissionError(f"OrderManager requires approved RiskDecision: {getattr(decision, 'reason', 'No decision')}")

        if not decision.stop_loss or decision.stop_loss <= 0:
            raise ValueError(f"PROTECTIVE_STOP_VIOLATION: Order {decision.symbol} missing required protective stop loss (got {getattr(decision, 'stop_loss', None)})")

        # Spot Mode SHORT Protection (defense-in-depth)
        is_spot = (
            getattr(self.risk_engine, "is_spot_mode", True)
            if self.risk_engine
            else getattr(self.execution_engine, "is_spot_mode", True)
        )
        if is_spot and decision.direction == SignalDirection.SHORT:
            logger.warning(
                f"OrderManager Spot Rejection: SHORT execution blocked for {decision.symbol}. (SIGNAL_ONLY)"
            )
            raise ValueError(f"Spot mode does not support SHORT execution on {decision.symbol}. (SIGNAL_ONLY)")

        side = OrderSide.BUY if decision.direction == SignalDirection.LONG else OrderSide.SELL
        sig_id = signal_id or f"sig_{decision.symbol}_{int(time.time())}"

        # 1. Idempotency Check anchored to signal timestamp
        sig_ts = getattr(decision, "timestamp", None)
        idem_key = self.generate_idempotency_key(
            strategy=strategy_name,
            symbol=decision.symbol,
            signal_id=sig_id,
            side=side.value,
            signal_timestamp=sig_ts,
        )
        if idem_key in self.idempotency_map:
            existing_order_id = self.idempotency_map[idem_key]
            existing_order = self.orders.get(existing_order_id)
            if existing_order and existing_order.status in [
                OrderStatus.SUBMITTING,
                OrderStatus.SUBMITTED,
                OrderStatus.ACKNOWLEDGED,
                OrderStatus.FILLED,
            ]:
                logger.warning(
                    f"Idempotent order duplicate detected for key {idem_key} -> returning existing order {existing_order_id}"
                )
                raise ValueError(f"DUPLICATE_ORDER_ATTEMPT: Order already in flight for {decision.symbol} (Key: {idem_key})")

        # 2. Symbol Filters Normalization
        is_valid, reject_reason, norm_price, norm_qty = symbol_filter_engine.normalize_and_validate(
            symbol=decision.symbol,
            price=decision.entry_price,
            quantity=decision.calculated_size,
        )
        if not is_valid:
            logger.error(f"Order rejected by symbol filter for {decision.symbol}: {reject_reason}")
            raise ValueError(f"SYMBOL_FILTER_REJECTION: {reject_reason}")

        decision.calculated_size = norm_qty
        decision.entry_price = norm_price

        # 3. Create Order in CREATED State
        order_id = f"ord_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc)
        order = Order(
            order_id=order_id,
            symbol=decision.symbol,
            order_type=OrderType.MARKET,
            side=side,
            quantity=norm_qty,
            price=norm_price,
            status=OrderStatus.CREATED,
            created_at=now,
            updated_at=now,
        )
        self.orders[order_id] = order
        self.idempotency_map[idem_key] = order_id
        self.idempotency_timestamps[idem_key] = now

        # 4. State Transition: SUBMITTING -> SUBMITTED
        order.status = OrderStatus.SUBMITTING
        order.updated_at = datetime.now(timezone.utc)

        # 5. Route to Execution Engine (Paper or Live)
        if not self.execution_engine:
            order.status = OrderStatus.ERROR
            raise RuntimeError("OrderManager has no bound ExecutionEngine.")

        try:
            order.status = OrderStatus.SUBMITTED
            # Non-blocking async execution dispatch
            if hasattr(self.execution_engine, "execute_market_order"):
                exec_order, fill, pos = await asyncio.to_thread(
                    self.execution_engine.execute_market_order,
                    decision=decision,
                    strategy_name=strategy_name,
                    order_id=order_id,
                    execution_style=get_settings().EXECUTION_STYLE,
                )
            else:
                exec_order, fill, pos = await self.execution_engine.submit_order(
                    decision=decision,
                    strategy_name=strategy_name,
                )

            # 6. State Transition: FILLED (or PARTIALLY_FILLED)
            order.status = OrderStatus.FILLED
            order.filled_quantity = fill.quantity
            order.average_fill_price = fill.price
            order.fee_paid = fill.fee
            order.updated_at = datetime.now(timezone.utc)

            # 7. Record Trade Counter strictly upon fill (AUDIT-05)
            if self.risk_engine and hasattr(self.risk_engine, "record_executed_trade"):
                self.risk_engine.record_executed_trade()

            # 8. Protective Stop Invariant (Section 14 & 15)
            await self._ensure_protective_stop(pos)

            if pos:
                if not getattr(pos, "signal_id", None):
                    pos.signal_id = sig_id
                if entry_context:
                    pos.entry_context = entry_context
                elif getattr(decision, "entry_context", None):
                    pos.entry_context = decision.entry_context
                if entry_arm:
                    pos.entry_arm = entry_arm
                elif getattr(decision, "entry_arm", None):
                    pos.entry_arm = decision.entry_arm
                if self.execution_engine and hasattr(self.execution_engine, "_persist_position"):
                    self.execution_engine._persist_position(pos)

            logger.info(
                f"OrderManager: Successfully executed {order.symbol} {order.side.value} "
                f"qty={order.filled_quantity} @ ${order.average_fill_price:.2f} (Status: FILLED)"
            )
            return order, fill, pos

        except Exception as e:
            order.status = OrderStatus.REJECTED if "Insufficient" in str(e) else OrderStatus.ERROR
            order.updated_at = datetime.now(timezone.utc)
            logger.error(f"OrderManager execution failed for {decision.symbol}: {e}")
            raise

    async def _ensure_protective_stop(self, position: Position) -> Optional[Order]:
        """
        Ensures open position has a real exchange-side protective stop submitted.
        Transits state SUBMITTING -> SUBMITTED on the exchange/broker.
        """
        if position.status != PositionStatus.OPEN or not position.stop_loss or position.stop_loss <= 0:
            return None

        stop_order_id = f"stop_{position.position_id}"
        side = OrderSide.SELL if position.side == PositionSide.LONG else OrderSide.BUY
        now = datetime.now(timezone.utc)
        stop_order = Order(
            order_id=stop_order_id,
            symbol=position.symbol,
            order_type=OrderType.STOP_LOSS,
            side=side,
            quantity=position.quantity,
            stop_price=position.stop_loss,
            status=OrderStatus.SUBMITTING,
            created_at=now,
            updated_at=now,
        )

        # Dispatch real stop order to execution engine if method exists
        if hasattr(self.execution_engine, "submit_stop_order"):
            try:
                await self.execution_engine.submit_stop_order(
                    symbol=position.symbol,
                    side=side,
                    quantity=position.quantity,
                    stop_price=position.stop_loss,
                )
                stop_order.status = OrderStatus.SUBMITTED
            except Exception as e:
                logger.error(f"Exchange protective stop failed for {position.symbol}: {e}")
                stop_order.status = OrderStatus.ERROR
                raise
        elif hasattr(self.execution_engine, "place_conditional_stop"):
            try:
                await self.execution_engine.place_conditional_stop(position)
                stop_order.status = OrderStatus.SUBMITTED
            except Exception as e:
                logger.error(f"Exchange conditional stop failed for {position.symbol}: {e}")
                stop_order.status = OrderStatus.ERROR
                raise
        else:
            # Paper execution registers stop price on position directly
            stop_order.status = OrderStatus.SUBMITTED

        stop_order.updated_at = datetime.now(timezone.utc)
        self.protective_stops[position.symbol] = stop_order
        logger.info(
            f"Protective Stop Verified for {position.symbol}: Stop @ ${position.stop_loss:.2f} (qty={position.quantity})"
        )
        return stop_order

    def remove_protective_stop(self, symbol: str) -> Optional[Order]:
        """Removes recorded protective stop when position is closed, preventing memory leak."""
        removed = self.protective_stops.pop(symbol, None)
        if removed:
            logger.info(f"Protective stop removed for closed position: {symbol}")
        return removed

    def prune_stale_cache(
        self, max_order_age_hours: float = 24.0, max_idempotency_age_hours: float = 1.0
    ) -> Tuple[int, int]:
        """
        Prunes completed orders older than 24h and idempotency entries older than 1h
        to prevent memory leaks in long-running 7/24 operation.
        """
        now = datetime.now(timezone.utc)
        terminal_statuses = {
            OrderStatus.FILLED,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
            OrderStatus.EXPIRED,
            OrderStatus.ERROR,
        }
        pruned_orders = 0
        for oid, order in list(self.orders.items()):
            if order.status in terminal_statuses:
                created = order.created_at if order.created_at.tzinfo else order.created_at.replace(tzinfo=timezone.utc)
                if (now - created).total_seconds() > (max_order_age_hours * 3600.0):
                    del self.orders[oid]
                    pruned_orders += 1

        pruned_idem = 0
        for key, ts in list(self.idempotency_timestamps.items()):
            ts_utc = ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
            if (now - ts_utc).total_seconds() > (max_idempotency_age_hours * 3600.0):
                self.idempotency_map.pop(key, None)
                self.idempotency_timestamps.pop(key, None)
                pruned_idem += 1

        if pruned_orders > 0 or pruned_idem > 0:
            logger.info(f"OrderManager cache pruned: {pruned_orders} old orders, {pruned_idem} old idempotency keys.")
        return pruned_orders, pruned_idem
    async def cancel_order(self, order_id: str) -> bool:
        """Cancels an order via the underlying execution engine and updates state."""
        order = self.orders.get(order_id)
        if hasattr(self.execution_engine, "cancel_order"):
            cancelled = await self.execution_engine.cancel_order(order_id)
            if cancelled and order:
                order.status = OrderStatus.CANCELED
                order.updated_at = datetime.now(timezone.utc)
                return True
        elif order and order.status in [OrderStatus.CREATED, OrderStatus.SUBMITTED]:
            order.status = OrderStatus.CANCELED
            order.updated_at = datetime.now(timezone.utc)
            return True
        return False

    async def cancel_all_orders(self) -> int:
        """Cancels all active non-terminal orders across broker/exchange."""
        count = 0
        active_statuses = [
            OrderStatus.CREATED,
            OrderStatus.SUBMITTING,
            OrderStatus.SUBMITTED,
            OrderStatus.ACKNOWLEDGED,
        ]
        # 1. Cancel in orders tracking
        for oid, order in list(self.orders.items()):
            if order.status in active_statuses:
                if await self.cancel_order(oid):
                    count += 1
        # 2. Cancel in execution engine broker if it maintains separate orders
        if hasattr(self.execution_engine, "orders"):
            for oid, order in list(self.execution_engine.orders.items()):
                if order.status in active_statuses:
                    if await self.cancel_order(oid):
                        count += 1
        return count

    def verify_protective_stop_invariant(self, open_positions: Dict[str, Position]) -> Tuple[bool, List[str]]:
        """Scans all open positions to verify that every single position has valid protective stop."""
        missing = []
        for symbol, pos in open_positions.items():
            if pos.status == PositionStatus.OPEN:
                if symbol not in self.protective_stops:
                    missing.append(symbol)
        return len(missing) == 0, missing


order_manager = OrderManager()
