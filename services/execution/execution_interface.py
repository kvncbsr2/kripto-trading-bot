from abc import ABC, abstractmethod
from typing import Optional, Tuple

from services.paper_trading.broker import PaperBroker
from shared.enums import OrderStatus
from shared.logging import get_logger
from shared.schemas import Fill, Order, Position, RiskDecision

logger = get_logger("execution-engine", service="execution")


class ExecutionEngine(ABC):
    @abstractmethod
    async def submit_order(
        self,
        decision: RiskDecision,
        strategy_name: str = "",
    ) -> Tuple[Order, Fill, Position]:
        pass

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        pass

    @abstractmethod
    async def get_order(self, order_id: str) -> Optional[Order]:
        pass


class PaperExecutionEngine(ExecutionEngine):
    """
    Paper execution implementation. Zero live exchange orders are submitted.
    """

    def __init__(self, broker: PaperBroker):
        self.broker = broker

    async def submit_order(
        self,
        decision: RiskDecision,
        strategy_name: str = "",
    ) -> Tuple[Order, Fill, Position]:
        return self.broker.execute_market_order(decision, strategy_name)

    async def cancel_order(self, order_id: str) -> bool:
        if order_id in self.broker.orders:
            self.broker.orders[order_id].status = OrderStatus.CANCELLED
            return True
        return False

    async def get_order(self, order_id: str) -> Optional[Order]:
        return self.broker.orders.get(order_id)
