from typing import Any, Dict, List

from agents.base_agent import AgentAnalysisResult, TradingAgent
from shared.enums import SignalDirection


class RiskAgent(TradingAgent):
    """
    AI Risk Agent.
    Evaluates aggregated risk factors and holds veto authority over orchestrator consensus.
    """

    def __init__(self, enabled: bool = True):
        super().__init__(name="RiskAgent", enabled=enabled)

    async def analyze(self, market_context: Dict[str, Any]) -> AgentAnalysisResult:
        flags: List[str] = []
        is_vetoed = False

        # Inspect any flags raised by other agents
        sub_flags = market_context.get("aggregated_flags", [])
        if "HEAVY_EXCHANGE_INFLOW_SELL_RISK" in sub_flags:
            flags.append("VETO_DUE_TO_MASSIVE_ONCHAIN_INFLOW")
            is_vetoed = True

        volatility = market_context.get("features", {}).get("realized_vol", 0.0)
        if volatility > 1.20:  # >120% annualized volatility
            flags.append("VETO_DUE_TO_EXTREME_VOLATILITY")
            is_vetoed = True

        return AgentAnalysisResult(
            agent_name=self.name,
            confidence=0.99 if is_vetoed else 0.85,
            recommended_direction=SignalDirection.FLAT if is_vetoed else SignalDirection.LONG,
            reasoning_summary="Risk Agent VETO: High systemic risk detected"
            if is_vetoed
            else "Risk Agent: Conditions within safe thresholds",
            risk_flags=flags,
            metadata={"is_vetoed": is_vetoed},
        )
