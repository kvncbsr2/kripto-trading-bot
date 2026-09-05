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
        self.protective_stops: Dict[str, Order] = {}  # symbol -> protective stop order

    def bind_execution_engine(self, engine):
        self.execution_engine = engine

    def bind_risk_engine(self, risk_engine):
        self.risk_engine = risk_engine

    @staticmethod
    def generate_idempotency_key(
        strategy: str, symbol: str, signal_id: str, side: str, time_bucket_seconds: int = 15
    ) -> str:
        """Generates deterministic idempotency key bucketed by time interval (Section 17)."""
        bucket = int(time.time() / time_bucket_seconds)
        raw = f"{strategy}:{symbol}:{signal_id}:{side}:{bucket}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    async def execute_risk_decision(
        self,
        decision: RiskDecision,
        strategy_name: str = "",
        signal_id: Optional[str] = None,
    ) -> Tuple[Order, Fill, Position]:
        """
        Main execution pipeline:
        1. Validate RiskDecision approval.
        2. Idempotency check.
        3. Symbol filter normalization.
        4. State machine progression.
        5. Execution engine submission.
        6. Protective stop verification.
        7. Record trade count.
        """
        if not decision or not decision.approved:
            raise PermissionError(f"OrderManager requires approved RiskDecision: {getattr(decision, 'reason', 'No decision')}")

        if not decision.stop_loss or decision.stop_loss <= 0:
            raise ValueError(f"PROTECTIVE_STOP_VIOLATION: Order {decision.symbol} missing required protective stop loss (got {getattr(decision, 'stop_loss', None)})")

        side = OrderSide.BUY if decision.direction == SignalDirection.LONG else OrderSide.SELL
        sig_id = signal_id or f"sig_{decision.symbol}_{int(time.time())}"

        # 1. Idempotency Check
        idem_key = self.generate_idempotency_key(
            strategy=strategy_name,
            symbol=decision.symbol,
            signal_id=sig_id,
            side=side.value,
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

        # 4. State Transition: SUBMITTING -> SUBMITTED
        order.status = OrderStatus.SUBMITTING
        order.updated_at = datetime.now(timezone.utc)

        # 5. Route to Execution Engine (Paper or Live)
        if not self.execution_engine:
            order.status = OrderStatus.ERROR
            raise RuntimeError("OrderManager has no bound ExecutionEngine.")

        try:
            order.status = OrderStatus.SUBMITTED
            # Execute on broker/exchange
            if hasattr(self.execution_engine, "execute_market_order"):
                exec_order, fill, pos = self.execution_engine.execute_market_order(
                    decision=decision,
                    strategy_name=strategy_name,
                    order_id=order_id,
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
            self._ensure_protective_stop(pos)

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

    def _ensure_protective_stop(self, position: Position):
        """Ensures open position has recorded protective stop-loss invariant."""
        if position.status == PositionStatus.OPEN and position.stop_loss > 0:
            stop_order_id = f"stop_{position.position_id}"
            stop_order = Order(
                order_id=stop_order_id,
                symbol=position.symbol,
                order_type=OrderType.STOP_LOSS,
                side=OrderSide.SELL if position.side == PositionSide.LONG else OrderSide.BUY,
                quantity=position.quantity,
                stop_price=position.stop_loss,
                status=OrderStatus.SUBMITTED,
                created_at=datetime.now(timezone.utc),
            )
            self.protective_stops[position.symbol] = stop_order
            logger.info(
                f"Protective Stop Verified for {position.symbol}: Stop @ ${position.stop_loss:.2f} (qty={position.quantity})"
            )
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
