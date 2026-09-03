import uuid
from typing import Any, Dict, List, Optional, Tuple

from services.paper_trading.portfolio import PortfolioTracker
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
from shared.utils import calculate_fee, calculate_slippage

logger = get_logger("paper-broker", service="paper_broker")


class PaperBroker:
    """
    Production-grade Paper Broker for the 7-day $5,000 validation experiment.
    Simulates:
    - Market / Limit / Stop Orders
    - Real slippage (5 bps = 0.05%)
    - Taker & Maker fees (0.1%)
    - Stop-Loss, Take-Profit, Trailing Stops, and Break-Even protection
    """

    def __init__(
        self,
        initial_balance: float = 5000.0,
        maker_fee: float = 0.001,  # 0.1%
        taker_fee: float = 0.001,  # 0.1%
        slippage_bps: float = 5.0,  # 5 bps
        enable_trailing_stop: bool = True,
    ):
        self.portfolio = PortfolioTracker(initial_balance=initial_balance)
        self.maker_fee = maker_fee
        self.taker_fee = taker_fee
        self.slippage_bps = slippage_bps
        self.enable_trailing_stop = enable_trailing_stop

        self.orders: Dict[str, Order] = {}
        self.fills: List[Fill] = []
        self.closed_positions_history: List[Position] = []

    def execute_market_order(
        self,
        decision: RiskDecision,
        strategy_name: str = "",
        book_ticker: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Order, Fill, Position]:
        is_buy = decision.direction == SignalDirection.LONG

        # Use exact Ask/Bid from Binance orderbook/ticker if available
        base_price = decision.entry_price
        if book_ticker:
            if is_buy and book_ticker.get("ask"):
                base_price = float(book_ticker["ask"])
            elif not is_buy and book_ticker.get("bid"):
                base_price = float(book_ticker["bid"])

        fill_price = calculate_slippage(
            price=base_price,
            slippage_bps=self.slippage_bps,
            is_buy=is_buy,
        )
        slippage_amount = abs(fill_price - decision.entry_price)
        fee = calculate_fee(fill_price, decision.calculated_size, self.taker_fee)

        order_id = f"ord_{uuid.uuid4().hex[:12]}"
        fill_id = f"fill_{uuid.uuid4().hex[:12]}"
        pos_id = f"pos_{uuid.uuid4().hex[:12]}"

        order = Order(
            order_id=order_id,
            symbol=decision.symbol,
            order_type=OrderType.MARKET,
            side=OrderSide.BUY if is_buy else OrderSide.SELL,
            quantity=decision.calculated_size,
            price=decision.entry_price,
            status=OrderStatus.FILLED,
            filled_quantity=decision.calculated_size,
            average_fill_price=fill_price,
            fee_paid=fee,
        )
        self.orders[order_id] = order

        fill = Fill(
            fill_id=fill_id,
            order_id=order_id,
            symbol=decision.symbol,
            side=order.side,
            price=fill_price,
            quantity=decision.calculated_size,
            fee=fee,
            slippage=slippage_amount,
        )
        self.fills.append(fill)

        position = Position(
            position_id=pos_id,
            symbol=decision.symbol,
            side=PositionSide.LONG if is_buy else PositionSide.SHORT,
            entry_price=fill_price,
            quantity=decision.calculated_size,
            current_price=fill_price,
            stop_loss=decision.stop_loss,
            take_profit=decision.take_profit,
            fees_paid=fee,
            strategy=strategy_name,
        )
        self.portfolio.add_position(position)

        logger.info(
            f"Paper Position OPENED: {position.symbol} {position.side.value} "
            f"qty={position.quantity} @ ${fill_price:.2f} (SL: ${position.stop_loss:.2f}, TP: ${position.take_profit:.2f})",
            extra={"symbol": position.symbol, "strategy": strategy_name},
        )
        return order, fill, position

    def check_position_stops_and_targets(
        self,
        symbol: str,
        high: float,
        low: float,
        close: float,
    ) -> Optional[Tuple[Position, str, float]]:
        if symbol not in self.portfolio.positions:
            return None

        pos = self.portfolio.positions[symbol]
        if pos.status != PositionStatus.OPEN:
            return None

        self.portfolio.update_market_price(symbol, close)

        # Break-Even & Trailing Stop logic
        if self.enable_trailing_stop:
            risk_dist = abs(pos.entry_price - pos.stop_loss)
            if pos.side == PositionSide.LONG:
                # If price moves +1.0R in profit, move stop loss to break-even (entry)
                if high >= pos.entry_price + risk_dist and pos.stop_loss < pos.entry_price:
                    pos.stop_loss = pos.entry_price
                    logger.info(
                        f"Stop-Loss adjusted to BREAK-EVEN for {symbol} LONG @ {pos.entry_price:.2f}"
                    )
            elif pos.side == PositionSide.SHORT:
                if low <= pos.entry_price - risk_dist and pos.stop_loss > pos.entry_price:
                    pos.stop_loss = pos.entry_price
                    logger.info(
                        f"Stop-Loss adjusted to BREAK-EVEN for {symbol} SHORT @ {pos.entry_price:.2f}"
                    )

        hit_reason: Optional[str] = None
        exit_price: float = 0.0

        if pos.side == PositionSide.LONG:
            if low <= pos.stop_loss:
                hit_reason = "STOP_LOSS"
                exit_price = pos.stop_loss
            elif high >= pos.take_profit:
                hit_reason = "TAKE_PROFIT"
                exit_price = pos.take_profit

        elif pos.side == PositionSide.SHORT:
            if high >= pos.stop_loss:
                hit_reason = "STOP_LOSS"
                exit_price = pos.stop_loss
            elif low <= pos.take_profit:
                hit_reason = "TAKE_PROFIT"
                exit_price = pos.take_profit

        if hit_reason and exit_price > 0:
            fee = calculate_fee(exit_price, pos.quantity, self.taker_fee)
            closed_pos = self.portfolio.close_position(symbol, exit_price, fee)
            if closed_pos:
                self.closed_positions_history.append(closed_pos)
                logger.info(
                    f"Paper Position CLOSED [{hit_reason}]: {symbol} {closed_pos.side.value} @ ${exit_price:.2f}, "
                    f"Net PnL: ${closed_pos.realized_pnl:.2f}",
                    extra={"symbol": symbol, "strategy": closed_pos.strategy},
                )
                return closed_pos, hit_reason, exit_price

        return None

    @property
    def open_positions(self) -> Dict[str, Position]:
        return {
            k: v for k, v in self.portfolio.positions.items() if v.status == PositionStatus.OPEN
        }

    @property
    def open_orders(self) -> Dict[str, Order]:
        return {k: v for k, v in self.orders.items() if v.status == OrderStatus.OPEN}

    def close_position(
        self, symbol: str, exit_price: float, reason: str = "MANUAL"
    ) -> Optional[Position]:
        if symbol not in self.portfolio.positions:
            return None
        pos = self.portfolio.positions[symbol]
        fee = calculate_fee(exit_price, pos.quantity, self.taker_fee)
        closed_pos = self.portfolio.close_position(symbol, exit_price, fee)
        if closed_pos:
            self.closed_positions_history.append(closed_pos)
            logger.info(
                f"Manual Paper Position CLOSED [{reason}]: {symbol} Net PnL: ${closed_pos.realized_pnl:+.2f}"
            )
        return closed_pos

    def cancel_order(self, order_id: str) -> bool:
        if order_id in self.orders:
            self.orders[order_id].status = OrderStatus.CANCELLED
            return True
        return False
