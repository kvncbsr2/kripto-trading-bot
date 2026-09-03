import uuid
from typing import Dict, List, Optional, Tuple

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

logger = get_logger("paper-broker", service="paper_trading")


class PaperBroker:
    def __init__(
        self,
        initial_balance: float = 10000.0,
        maker_fee_rate: float = 0.0002,  # 0.02%
        taker_fee_rate: float = 0.0005,  # 0.05%
        slippage_bps: float = 2.0,  # 0.02% slippage
    ):
        self.portfolio = PortfolioTracker(initial_balance=initial_balance)
        self.maker_fee_rate = maker_fee_rate
        self.taker_fee_rate = taker_fee_rate
        self.slippage_bps = slippage_bps
        self.orders: Dict[str, Order] = {}
        self.fills: List[Fill] = []
        self.closed_positions_history: List[Position] = []

    def execute_market_order(
        self,
        decision: RiskDecision,
        strategy_name: str = "",
    ) -> Tuple[Order, Fill, Position]:
        """
        Simulates execution of an approved market entry order.
        Applies slippage and taker fee.
        """
        is_buy = decision.direction == SignalDirection.LONG
        fill_price = calculate_slippage(
            price=decision.entry_price,
            slippage_bps=self.slippage_bps,
            is_buy=is_buy,
        )
        slippage_amount = abs(fill_price - decision.entry_price)
        fee = calculate_fee(fill_price, decision.calculated_size, self.taker_fee_rate)

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
            f"Paper Position OPENED: {position.symbol} {position.side.value} size={position.quantity} @ {fill_price:.2f} (SL: {position.stop_loss:.2f}, TP: {position.take_profit:.2f})",
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
        """
        Checks whether active position hit Stop-Loss or Take-Profit.
        Returns (closed_position, reason, exit_price) if closed.
        """
        if symbol not in self.portfolio.positions:
            return None

        pos = self.portfolio.positions[symbol]
        if pos.status != PositionStatus.OPEN:
            return None

        self.portfolio.update_market_price(symbol, close)

        hit_reason: Optional[str] = None
        exit_price: float = 0.0

        if pos.side == PositionSide.LONG:
            # Check Stop Loss (Low <= Stop)
            if low <= pos.stop_loss:
                hit_reason = "STOP_LOSS"
                exit_price = pos.stop_loss
            # Check Take Profit (High >= TP)
            elif high >= pos.take_profit:
                hit_reason = "TAKE_PROFIT"
                exit_price = pos.take_profit

        elif pos.side == PositionSide.SHORT:
            # Check Stop Loss (High >= Stop)
            if high >= pos.stop_loss:
                hit_reason = "STOP_LOSS"
                exit_price = pos.stop_loss
            # Check Take Profit (Low <= TP)
            elif low <= pos.take_profit:
                hit_reason = "TAKE_PROFIT"
                exit_price = pos.take_profit

        if hit_reason and exit_price > 0:
            fee = calculate_fee(exit_price, pos.quantity, self.taker_fee_rate)
            closed_pos = self.portfolio.close_position(symbol, exit_price, fee)
            if closed_pos:
                self.closed_positions_history.append(closed_pos)
                logger.info(
                    f"Paper Position CLOSED [{hit_reason}]: {symbol} {closed_pos.side.value} @ {exit_price:.2f}, Realized PnL: ${closed_pos.realized_pnl:.2f}",
                    extra={"symbol": symbol, "strategy": closed_pos.strategy},
                )
                return closed_pos, hit_reason, exit_price

        return None
