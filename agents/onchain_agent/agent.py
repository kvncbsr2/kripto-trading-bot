from typing import Any, Dict

from agents.base_agent import AgentAnalysisResult, TradingAgent
from shared.enums import SignalDirection


class OnchainAgent(TradingAgent):
    def __init__(self, enabled: bool = False):
        super().__init__(name="OnchainAgent", enabled=enabled)

    async def analyze(self, market_context: Dict[str, Any]) -> AgentAnalysisResult:
        net_inflows_usd = market_context.get("exchange_net_inflows", 0.0)
        flags = []
        if net_inflows_usd > 50_000_000:
            flags.append("HEAVY_EXCHANGE_INFLOW_SELL_RISK")

        return AgentAnalysisResult(
            agent_name=self.name,
            confidence=0.6,
            recommended_direction=SignalDirection.FLAT,
            reasoning_summary=f"Exchange net inflows: ${net_inflows_usd:,.0f}",
            risk_flags=flags,
            metadata={"net_inflows_usd": net_inflows_usd},
        )
