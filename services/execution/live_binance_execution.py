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

import uuid
from datetime import datetime, timezone
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
    ):
        self.api_key = api_key or settings.BINANCE_API_KEY
        self.api_secret = api_secret or settings.BINANCE_API_SECRET
        self.is_armed = armed if armed is not None else getattr(settings, "LIVE_TRADING_ARMED", False)

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
        logger.warning("🚨 LIVE TRADING ENABLED AND ARMED: Authenticated Binance Spot Execution Active.")

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

        binance_order_id = str(raw_res.get("id"))
        fill_price = float(raw_res.get("average") or raw_res.get("price") or norm_price)
        filled_qty = float(raw_res.get("filled") or norm_qty)
        fee_cost = float(raw_res.get("fee", {}).get("cost", 0.0)) if raw_res.get("fee") else 0.0

        order = Order(
            order_id=binance_order_id,
            symbol=decision.symbol,
            order_type=OrderType.MARKET,
            side=OrderSide.BUY,
            quantity=filled_qty,
            price=fill_price,
            status=OrderStatus.FILLED,
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
        )

        # 2. INVARIANT P0-RISK-002: Submit Exchange-Side Protective Stop
        stop_client_id = f"ks_{uuid.uuid4().hex[:16]}"
        try:
            logger.info(
                f"Submitting LIVE Exchange-Side Protective Stop on Binance: {decision.symbol} STOP_LOSS_LIMIT @ ${decision.stop_loss:.2f}"
            )
            # Binance Spot STOP_LOSS_LIMIT order: stopPrice triggers order at limit price
            stop_res = await self.client.create_order(
                symbol=decision.symbol,
                type="STOP_LOSS_LIMIT",
                side="sell",
                amount=filled_qty,
                price=round(decision.stop_loss * 0.999, 4),  # Limit slightly below trigger to ensure execution
                params={
                    "stopPrice": decision.stop_loss,
                    "newClientOrderId": stop_client_id,
                    "timeInForce": "GTC",
                },
            )
            stop_order_id = str(stop_res.get("id"))
            logger.info(f"Exchange-Side Protective Stop ACCEPTED by Binance: ID={stop_order_id}")
        except Exception as stop_err:
            # FAIL-CLOSED: An unprotected live position is prohibited!
            logger.critical(
                f"FATAL SECURITY VIOLATION: Protective stop failed on Binance ({stop_err}). Emergency liquidating position to fail closed!"
            )
            try:
                # Emergency close
                await self.client.create_order(
                    symbol=decision.symbol,
                    type="market",
                    side="sell",
                    amount=filled_qty,
                )
            except Exception as close_err:
                logger.critical(f"Emergency liquidation also failed: {close_err}")
            raise RuntimeError(
                f"FAIL-CLOSED INVARIANT: Protective stop rejected by Binance ({stop_err}). Position was emergency-liquidated."
            )

        self.open_positions[decision.symbol] = position
        return order, fill, position

    async def cancel_order(self, order_id: str, symbol: Optional[str] = None) -> bool:
        """Cancels order on Binance exchange."""
        self._verify_safety_gates()
        try:
            res = await self.client.cancel_order(order_id, symbol=symbol)
            if order_id in self.orders:
                self.orders[order_id].status = OrderStatus.CANCELED
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
            return Order(
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
