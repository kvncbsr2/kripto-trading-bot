from abc import ABC, abstractmethod
from typing import Optional, Tuple

from shared.schemas import Fill, Order, Position, RiskDecision


class ExecutionEngine(ABC):
    """
    Abstract Base Class defining the Authoritative Execution Interface for KRIPTO AGENT.
    All execution engines (PaperExecutionEngine, LiveBinanceExecutionEngine) implement this contract.
    """

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
