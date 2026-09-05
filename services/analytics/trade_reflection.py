"""
Post-Trade Reflection & Episodic Memory Engine.
Inspired by Xtra-Computing/CryptoTrade.

Analyzes closed trades to extract causal reasons for success or failure.
Maintains a rolling episodic memory buffer to prevent repeating recent mistakes.
"""

import json
from collections import deque
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional

from pydantic import BaseModel, Field

from core.llm.llm_provider import BaseLLMProvider, get_llm_provider
from shared.enums import SignalDirection
from shared.logging import get_logger

logger = get_logger("trade-reflection", service="analytics")


class TradeReflectionRecord(BaseModel):
    reflection_id: str
    symbol: str
    direction: SignalDirection
    entry_price: float
    exit_price: float
    realized_pnl: float
    pnl_pct: float
    exit_reason: str
    duration_minutes: float
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    lesson_learned: str
    root_cause: str
    recommendation: str


class TradeReflectionEngine:
    """
    Episodic Trade Memory and Post-Trade Reflection Evaluator.
    """

    def __init__(
        self,
        max_memory_size: int = 50,
        llm_provider: Optional[BaseLLMProvider] = None,
    ):
        self.memory: Deque[TradeReflectionRecord] = deque(maxlen=max_memory_size)
        self.llm = llm_provider or get_llm_provider()

    async def reflect_on_closed_trade(
        self,
        trade_id: str,
        symbol: str,
        direction: SignalDirection,
        entry_price: float,
        exit_price: float,
        realized_pnl: float,
        pnl_pct: float,
        exit_reason: str,
        duration_minutes: float = 0.0,
        market_context_at_exit: Optional[Dict[str, Any]] = None,
    ) -> TradeReflectionRecord:
        """
        Synthesizes causal analysis on why this trade succeeded or failed.
        """
        is_win = realized_pnl > 0

        system_prompt = (
            "You are a Senior Trading Post-Mortem Analyst at an algorithmic fund. "
            "Analyze the closed trade objectively. Extract the root cause and a concrete lesson. "
            "Output JSON with keys: 'root_cause', 'lesson_learned', 'recommendation'."
        )

        user_prompt = (
            f"Trade Details:\n"
            f"- Symbol: {symbol}\n"
            f"- Direction: {direction.value}\n"
            f"- Entry: {entry_price}, Exit: {exit_price}\n"
            f"- Realized PnL: ${realized_pnl:.2f} ({pnl_pct:+.2f}%)\n"
            f"- Exit Reason: {exit_reason}\n"
            f"- Duration: {duration_minutes:.1f} mins\n"
            f"- Outcome: {'PROFITABLE' if is_win else 'LOSS'}\n"
            "What is the lesson to prevent future losses or reinforce this edge?"
        )

        try:
            raw_res = await self.llm.generate(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=0.2,
                json_mode=True,
            )
            data = json.loads(raw_res)
            root_cause = data.get("root_cause", "Standard trade execution completed.")
            lesson = data.get("lesson_learned", "Maintain risk discipline.")
            rec = data.get("recommendation", "Continue following edge.")
        except Exception as e:
            logger.debug(f"LLM reflection fallback used: {e}")
            if is_win:
                root_cause = "Trade reached target within risk parameters."
                lesson = "Trend continuation and R/R symmetry respected."
                rec = "Reinvest gains according to risk sizing rules."
            else:
                root_cause = f"Hit {exit_reason} due to counter-trend volatility."
                lesson = "Tighten stop or avoid entering near macro resistance/support."
                rec = "Require secondary volume confirmation before next entry."

        record = TradeReflectionRecord(
            reflection_id=f"refl-{trade_id}-{int(datetime.now(timezone.utc).timestamp())}",
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            exit_price=exit_price,
            realized_pnl=realized_pnl,
            pnl_pct=pnl_pct,
            exit_reason=exit_reason,
            duration_minutes=duration_minutes,
            lesson_learned=lesson,
            root_cause=root_cause,
            recommendation=rec,
        )

        self.memory.append(record)
        logger.info(f"Recorded trade reflection for {symbol} ({'WIN' if is_win else 'LOSS'}): {lesson}")
        return record

    def get_recent_reflections(self, limit: int = 5) -> List[Dict[str, Any]]:
        """Returns the most recent N reflection records."""
        items = list(self.memory)[-limit:]
        return [item.model_dump() for item in reversed(items)]

    def get_relevant_insights(
        self, symbol: str, direction: SignalDirection
    ) -> Optional[str]:
        """
        Retrieves episodic insights for a specific symbol & direction before opening a new trade.
        """
        matching = [
            r for r in reversed(list(self.memory))
            if r.symbol == symbol and r.direction == direction
        ]
        if not matching:
            return None

        recent = matching[0]
        if recent.realized_pnl < 0:
            return f"CAUTION: Last {direction.value} trade on {symbol} hit {recent.exit_reason}. Lesson: {recent.lesson_learned}"
        return f"PREVIOUS WIN: Last {direction.value} on {symbol} was profitable. Note: {recent.lesson_learned}"


# Global Singleton
trade_reflection_engine = TradeReflectionEngine()
