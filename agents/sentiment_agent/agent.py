from typing import Any, Dict

from agents.base_agent import AgentAnalysisResult, TradingAgent
from shared.enums import SignalDirection


class SentimentAgent(TradingAgent):
    def __init__(self, enabled: bool = False):
        super().__init__(name="SentimentAgent", enabled=enabled)

    async def analyze(self, market_context: Dict[str, Any]) -> AgentAnalysisResult:
        # Placeholder for social media sentiment / LunarCrush / Twitter feeds
        sentiment_score = market_context.get("sentiment_score", 0.0)  # -1.0 to +1.0
        direction = SignalDirection.FLAT
        flags = []

        if sentiment_score > 0.4:
            direction = SignalDirection.LONG
        elif sentiment_score < -0.4:
            direction = SignalDirection.SHORT
            flags.append("ELEVATED_BEARISH_FEAR")

        return AgentAnalysisResult(
            agent_name=self.name,
            confidence=0.6,
            recommended_direction=direction,
            reasoning_summary=f"Sentiment score: {sentiment_score:.2f}",
            risk_flags=flags,
            metadata={"sentiment_score": sentiment_score},
        )
