from typing import Any, Dict, List, Optional

from agents.base_agent import AgentAnalysisResult
from agents.debate.bull_bear_engine import BullBearDebateEngine
from agents.macro_agent.agent import MacroAgent
from agents.onchain_agent.agent import OnchainAgent
from agents.risk_agent.agent import RiskAgent
from agents.sentiment_agent.agent import SentimentAgent
from agents.technical_agent.agent import TechnicalAgent
from services.analytics.trade_reflection import trade_reflection_engine
from shared.enums import SignalDirection
from shared.logging import get_logger

logger = get_logger("agent-orchestrator", service="orchestrator")


class AgentOrchestrator:
    """
    Multi-Agent Orchestration Layer.
    Synthesizes signals from technical, sentiment, macro, and onchain agents.
    Features:
    - Dialectic Bull vs Bear Debate Engine (TauricResearch / auronsun inspired)
    - Episodic Reflection Memory Check (CryptoTrade inspired)
    - AI Risk Agent Veto & Safety Checks
    """

    def __init__(self, debate_enabled: bool = True):
        self.technical_agent = TechnicalAgent(enabled=True)
        self.sentiment_agent = SentimentAgent(enabled=False)
        self.macro_agent = MacroAgent(enabled=False)
        self.onchain_agent = OnchainAgent(enabled=False)
        self.risk_agent = RiskAgent(enabled=True)
        self.debate_engine = BullBearDebateEngine() if debate_enabled else None
        self.reflection_engine = trade_reflection_engine

    async def run_consensus(self, market_context: Dict[str, Any]) -> Dict[str, Any]:
        results: List[AgentAnalysisResult] = []

        # 1. Gather inputs from analytical agents
        tech_res = await self.technical_agent.analyze(market_context)
        results.append(tech_res)

        if self.sentiment_agent.enabled:
            results.append(await self.sentiment_agent.analyze(market_context))
        if self.macro_agent.enabled:
            results.append(await self.macro_agent.analyze(market_context))
        if self.onchain_agent.enabled:
            results.append(await self.onchain_agent.analyze(market_context))

        # 2. Collect all raised risk flags
        all_flags = []
        for r in results:
            all_flags.extend(r.risk_flags)
        market_context["aggregated_flags"] = all_flags

        # 3. Run Risk Agent with full context
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
                "debate": None,
                "memory_insight": None,
            }

        # 4. Synthesize consensus from analytical agents
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

        # 5. Run Dialectic Bull vs Bear Debate
        debate_verdict = None
        if self.debate_engine:
            try:
                debate_verdict = await self.debate_engine.run_debate(market_context)
                # If debate has high conviction that agrees with direction, boost confidence slightly
                if direction != SignalDirection.FLAT and debate_verdict.direction == direction:
                    confidence = min(0.95, confidence * 1.1)
                elif direction != SignalDirection.FLAT and debate_verdict.direction != SignalDirection.FLAT and debate_verdict.direction != direction:
                    # Debate opposes initial signal -> cross-examination uncertainty dampens confidence
                    confidence = max(0.40, confidence * 0.75)
                    logger.info(f"Debate challenged consensus: proposed {direction.value} vs debate {debate_verdict.direction.value}")
            except Exception as e:
                logger.warning(f"Debate engine execution error: {e}")

        # 6. Check Episodic Trade Reflection Memory
        symbol = market_context.get("symbol", "BTC/USDT")
        memory_insight: Optional[str] = None
        if self.reflection_engine and direction != SignalDirection.FLAT:
            memory_insight = self.reflection_engine.get_relevant_insights(symbol, direction)
            if memory_insight and "CAUTION" in memory_insight:
                logger.info(f"Episodic memory caveat noted for {symbol}: {memory_insight}")

        approved = direction != SignalDirection.FLAT and confidence >= 0.55

        return {
            "approved": approved,
            "direction": direction,
            "confidence": round(confidence, 2),
            "reasoning": f"Multi-Agent Consensus: {direction.value} (Confidence: {confidence:.2f})",
            "agent_results": [r.model_dump() for r in results],
            "debate": debate_verdict.model_dump() if debate_verdict else None,
            "memory_insight": memory_insight,
        }
