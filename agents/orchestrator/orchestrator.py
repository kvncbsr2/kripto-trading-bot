from typing import Any, Dict, List

from agents.base_agent import AgentAnalysisResult
from agents.macro_agent.agent import MacroAgent
from agents.onchain_agent.agent import OnchainAgent
from agents.risk_agent.agent import RiskAgent
from agents.sentiment_agent.agent import SentimentAgent
from agents.technical_agent.agent import TechnicalAgent
from shared.enums import SignalDirection
from shared.logging import get_logger

logger = get_logger("agent-orchestrator", service="orchestrator")


class AgentOrchestrator:
    """
    Multi-Agent Orchestration Layer.
    Synthesizes signals from technical, sentiment, macro, and onchain agents.
    Subject to final validation and veto by RiskAgent and deterministic RiskEngine.
    """

    def __init__(self):
        self.technical_agent = TechnicalAgent(enabled=True)
        self.sentiment_agent = SentimentAgent(enabled=False)
        self.macro_agent = MacroAgent(enabled=False)
        self.onchain_agent = OnchainAgent(enabled=False)
        self.risk_agent = RiskAgent(enabled=True)

    async def run_consensus(self, market_context: Dict[str, Any]) -> Dict[str, Any]:
        results: List[AgentAnalysisResult] = []

        # Gather inputs from analytical agents
        tech_res = await self.technical_agent.analyze(market_context)
        results.append(tech_res)

        if self.sentiment_agent.enabled:
            results.append(await self.sentiment_agent.analyze(market_context))
        if self.macro_agent.enabled:
            results.append(await self.macro_agent.analyze(market_context))
        if self.onchain_agent.enabled:
            results.append(await self.onchain_agent.analyze(market_context))

        # Collect all raised risk flags
        all_flags = []
        for r in results:
            all_flags.extend(r.risk_flags)
        market_context["aggregated_flags"] = all_flags

        # Run Risk Agent with full context
        risk_res = await self.risk_agent.analyze(market_context)
        results.append(risk_res)

        # Check for Risk Agent Veto
        is_vetoed = risk_res.metadata.get("is_vetoed", False)
        if is_vetoed:
            logger.warning(f"Consensus VETOED by Risk Agent: {risk_res.reasoning_summary}")
            return {
                "approved": False,
                "direction": SignalDirection.FLAT,
                "confidence": 0.0,
                "reasoning": f"Vetoed: {risk_res.reasoning_summary}",
                "agent_results": [r.model_dump() for r in results],
            }

        # Otherwise synthesize consensus from Technical + other active agents
        long_score = sum(
            r.confidence for r in results if r.recommended_direction == SignalDirection.LONG
        )
        short_score = sum(
            r.confidence for r in results if r.recommended_direction == SignalDirection.SHORT
        )

        if long_score > short_score and long_score >= 0.7:
            direction = SignalDirection.LONG
            confidence = min(1.0, long_score / max(1, len(results)))
        elif short_score > long_score and short_score >= 0.7:
            direction = SignalDirection.SHORT
            confidence = min(1.0, short_score / max(1, len(results)))
        else:
            direction = SignalDirection.FLAT
            confidence = 0.5

        return {
            "approved": direction != SignalDirection.FLAT,
            "direction": direction,
            "confidence": round(confidence, 2),
            "reasoning": f"Multi-Agent Consensus: {direction.value} (Confidence: {confidence:.2f})",
            "agent_results": [r.model_dump() for r in results],
        }
