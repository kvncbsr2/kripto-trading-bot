from typing import Optional, Tuple

from services.execution.execution_interface import ExecutionEngine
from shared.config import get_settings
from shared.logging import get_logger
from shared.schemas import Fill, Order, Position, RiskDecision

logger = get_logger("live-binance-execution", service="execution")
settings = get_settings()


class BinanceLiveExecutionEngine(ExecutionEngine):
    """
    Architectural interface for future live Binance execution.
    STRICTLY LOCKED AND PROTECTED DURING VALIDATION PHASES.
    Any attempt to initialize or execute raises RuntimeError.
    """

    def __init__(self, api_key: Optional[str] = None, api_secret: Optional[str] = None):
        self.api_key = api_key
        self.api_secret = api_secret

        # Hard safety lock
        if not settings.LIVE_TRADING or True:
            logger.critical("SECURITY GUARDRAIL ENGAGED: Live trading execution engine is locked.")
            raise RuntimeError("LIVE TRADING IS LOCKED DURING VALIDATION.")

    async def submit_order(
        self, decision: RiskDecision, strategy_name: str = ""
    ) -> Tuple[Order, Fill, Position]:
        raise RuntimeError("SECURITY VIOLATION: LIVE TRADING IS LOCKED DURING VALIDATION.")

    async def cancel_order(self, order_id: str) -> bool:
        raise RuntimeError("SECURITY VIOLATION: LIVE TRADING IS LOCKED DURING VALIDATION.")

    async def get_order(self, order_id: str) -> Optional[Order]:
        raise RuntimeError("SECURITY VIOLATION: LIVE TRADING IS LOCKED DURING VALIDATION.")
