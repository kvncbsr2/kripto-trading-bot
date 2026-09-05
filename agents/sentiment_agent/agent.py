from typing import Any, Dict

from agents.base_agent import AgentAnalysisResult, TradingAgent
from services.market_data.crypto_sentiment import sentiment_service
from shared.enums import SignalDirection


class SentimentAgent(TradingAgent):
    def __init__(self, enabled: bool = False):
        super().__init__(name="SentimentAgent", enabled=enabled)

    async def analyze(self, market_context: Dict[str, Any]) -> AgentAnalysisResult:
        # Check if sentiment is provided in context or fetch from live service
        sentiment_data = market_context.get("sentiment")
        if not sentiment_data:
            sentiment_data = await sentiment_service.get_fear_and_greed_index()

        fng_val = sentiment_data.get("value", 50)
        classification = sentiment_data.get("classification", "Neutral")
        sentiment_score = sentiment_data.get("normalized_score", (fng_val - 50) / 50.0)

        # In case explicit override sentiment_score is provided in market_context
        if "sentiment_score" in market_context:
            sentiment_score = float(market_context["sentiment_score"])

        direction = SignalDirection.FLAT
        flags = []
        confidence = 0.60

        # Contrarian & Momentum Sentiment Rules
        if sentiment_score > 0.50:  # Extreme Greed (>75)
            # High greed can be continuation or overheated top
            direction = SignalDirection.LONG
            confidence = 0.65
            if sentiment_score > 0.70:
                flags.append("EXTREME_GREED_OVERHEATED")
        elif sentiment_score < -0.50:  # Extreme Fear (<25)
            # Contrarian bounce opportunity or severe downtrend
            direction = SignalDirection.SHORT
            confidence = 0.65
            flags.append("ELEVATED_BEARISH_FEAR")
        else:
            direction = SignalDirection.FLAT
            confidence = 0.50

        return AgentAnalysisResult(
            agent_name=self.name,
            confidence=confidence,
            recommended_direction=direction,
            reasoning_summary=f"Crypto Sentiment: {fng_val}/100 ({classification})",
            risk_flags=flags,
            metadata={
                "sentiment_score": round(sentiment_score, 2),
                "fng_value": fng_val,
                "fng_classification": classification,
            },
        )
