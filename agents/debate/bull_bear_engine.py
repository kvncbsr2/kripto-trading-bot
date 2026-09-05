"""
Bull vs Bear Dialectic Debate Engine.
Inspired by TauricResearch/TradingAgents and auronsun/TradingAgents-crypto.

Pits an evidence-based Bullish Researcher against a Bearish Researcher.
The Synthesizer reconciles their arguments to prevent confirmation bias and LLM overconfidence.
"""

import json
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from core.llm.llm_provider import BaseLLMProvider, get_llm_provider
from shared.enums import SignalDirection
from shared.logging import get_logger

logger = get_logger("debate-engine", service="agent")


class DebateVerdict(BaseModel):
    direction: SignalDirection
    confidence: float = Field(ge=0.0, le=1.0)
    bull_thesis: str
    bear_thesis: str
    synthesis: str
    risk_notes: List[str] = Field(default_factory=list)
    verdict_metadata: Dict[str, Any] = Field(default_factory=dict)


class BullBearDebateEngine:
    """
    Executes a structured dialectic debate between Bull and Bear personas.
    """

    def __init__(self, llm_provider: Optional[BaseLLMProvider] = None):
        self.llm = llm_provider or get_llm_provider()

    def _extract_evidence(self, market_context: Dict[str, Any]) -> Dict[str, Any]:
        features = market_context.get("features", {})
        sentiment = market_context.get("sentiment", {})
        derivatives = market_context.get("derivatives", {})
        orderbook = market_context.get("orderbook", {})

        return {
            "symbol": market_context.get("symbol", "BTC/USDT"),
            "current_price": market_context.get("current_price", 0.0),
            "rsi": features.get("rsi", 50.0),
            "ema_20": features.get("ema_20", 0.0),
            "ema_50": features.get("ema_50", 0.0),
            "realized_vol": features.get("realized_vol", 0.0),
            "fng_index": sentiment.get("value", 50),
            "fng_label": sentiment.get("classification", "Neutral"),
            "long_short_ratio": derivatives.get("long_short_ratio", 1.0),
            "taker_ratio": derivatives.get("taker_buy_sell_ratio", 1.0),
            "spread_bps": orderbook.get("spread_bps", 0.0),
        }

    async def run_debate(self, market_context: Dict[str, Any]) -> DebateVerdict:
        evidence = self._extract_evidence(market_context)
        
        # Formulate structured prompt for dialectic debate
        system_prompt = (
            "You are the Chief Market Debate Moderator in an algorithmic quantitative crypto hedge fund. "
            "You must stage a dialectic debate between a Bullish Researcher (advocating LONG) "
            "and a Bearish Researcher (advocating SHORT). "
            "Cross-examine both arguments using the provided hard indicators, derivatives ratios, and sentiment. "
            "Output valid JSON with the exact keys: 'verdict' (LONG, SHORT, or FLAT), 'confidence' (0.0 to 1.0), "
            "'bull_thesis', 'bear_thesis', 'synthesis', 'key_risk'."
        )

        user_prompt = (
            f"Market Evidence for {evidence['symbol']}:\n"
            f"- Price: {evidence['current_price']}\n"
            f"- RSI (14): {evidence['rsi']:.1f}\n"
            f"- EMA20 vs EMA50: {evidence['ema_20']:.2f} vs {evidence['ema_50']:.2f}\n"
            f"- Realized Volatility: {evidence['realized_vol']:.2%}\n"
            f"- Crypto Fear & Greed: {evidence['fng_index']} ({evidence['fng_label']})\n"
            f"- Binance Futures Long/Short Account Ratio: {evidence['long_short_ratio']:.2f}\n"
            f"- Taker Buy/Sell Volume Ratio: {evidence['taker_ratio']:.2f}\n"
            f"- Orderbook Spread: {evidence['spread_bps']:.2f} bps\n"
            "Deliver the debate synthesis and final consensus verdict."
        )

        try:
            raw_response = await self.llm.generate(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.2,
                json_mode=True,
            )
            data = json.loads(raw_response)

            raw_verdict = str(data.get("verdict", "FLAT")).upper()
            if raw_verdict == "LONG":
                direction = SignalDirection.LONG
            elif raw_verdict == "SHORT":
                direction = SignalDirection.SHORT
            else:
                direction = SignalDirection.FLAT

            confidence = float(data.get("confidence", 0.5))
            confidence = max(0.0, min(1.0, confidence))

            return DebateVerdict(
                direction=direction,
                confidence=confidence,
                bull_thesis=data.get("bull_thesis", "Bull thesis presented."),
                bear_thesis=data.get("bear_thesis", "Bear thesis presented."),
                synthesis=data.get("synthesis", "Consensus reached."),
                risk_notes=[data.get("key_risk")] if data.get("key_risk") else [],
                verdict_metadata={"raw_verdict": raw_verdict, "evidence": evidence},
            )

        except Exception as e:
            logger.warning(f"Debate LLM parsing error: {e}. Executing rule-based dialectic synthesis.")
            
            # Rule-based dialectic fallback
            rsi = evidence["rsi"]
            bull_score = 0
            bear_score = 0

            bull_points = []
            bear_points = []

            if evidence["ema_20"] > evidence["ema_50"]:
                bull_score += 1
                bull_points.append("EMA20 is above EMA50 indicating bullish trend continuation.")
            else:
                bear_score += 1
                bear_points.append("EMA20 is below EMA50 indicating downward pressure.")

            if rsi < 40:
                bull_score += 1
                bull_points.append(f"RSI at {rsi:.1f} shows oversold bounce potential.")
            elif rsi > 65:
                bear_score += 1
                bear_points.append(f"RSI at {rsi:.1f} shows overextended risk.")

            if evidence["taker_ratio"] > 1.15:
                bull_score += 1
                bull_points.append("Takers are aggressively buying (ratio > 1.15).")
            elif evidence["taker_ratio"] < 0.85:
                bear_score += 1
                bear_points.append("Takers are aggressively dumping (ratio < 0.85).")

            if bull_score > bear_score and bull_score >= 2:
                final_dir = SignalDirection.LONG
                conf = 0.72
            elif bear_score > bull_score and bear_score >= 2:
                final_dir = SignalDirection.SHORT
                conf = 0.72
            else:
                final_dir = SignalDirection.FLAT
                conf = 0.50

            return DebateVerdict(
                direction=final_dir,
                confidence=conf,
                bull_thesis=" ".join(bull_points) or "Momentum neutral.",
                bear_thesis=" ".join(bear_points) or "Downside protected.",
                synthesis=f"Dialectic debate resolved {final_dir.value} with confidence {conf:.2f}.",
                risk_notes=["Volatility monitoring active."],
                verdict_metadata={"rule_based_fallback": True, "evidence": evidence},
            )
