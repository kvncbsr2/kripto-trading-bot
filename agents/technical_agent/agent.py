from typing import Any, Dict

from agents.base_agent import AgentAnalysisResult, TradingAgent
from shared.enums import SignalDirection


class TechnicalAgent(TradingAgent):
    def __init__(self, enabled: bool = False):
        super().__init__(name="TechnicalAgent", enabled=enabled)

    async def analyze(self, market_context: Dict[str, Any]) -> AgentAnalysisResult:
        features = market_context.get("features", {})
        rsi = features.get("rsi", 50.0)
        ema_20 = features.get("ema_20", 0.0)
        ema_50 = features.get("ema_50", 0.0)

        direction = SignalDirection.FLAT
        confidence = 0.5
        reasoning = "Technical parameters neutral."

        if ema_20 > ema_50 and 45.0 <= rsi <= 65.0:
            direction = SignalDirection.LONG
            confidence = 0.75
            reasoning = f"EMA20 > EMA50 bullish continuation with healthy RSI {rsi:.1f}."
        elif ema_20 < ema_50 and 35.0 <= rsi <= 55.0:
            direction = SignalDirection.SHORT
            confidence = 0.75
            reasoning = f"EMA20 < EMA50 bearish continuation with bearish RSI {rsi:.1f}."

        return AgentAnalysisResult(
            agent_name=self.name,
            confidence=confidence,
            recommended_direction=direction,
            reasoning_summary=reasoning,
            risk_flags=[],
            metadata={"rsi": rsi, "ema_20": ema_20, "ema_50": ema_50},
        )
