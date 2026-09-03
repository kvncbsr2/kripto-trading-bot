from abc import ABC, abstractmethod
from typing import Any, Dict, List

from pydantic import BaseModel, Field

from shared.enums import SignalDirection


class AgentAnalysisResult(BaseModel):
    agent_name: str
    confidence: float = Field(ge=0.0, le=1.0)
    recommended_direction: SignalDirection = SignalDirection.FLAT
    reasoning_summary: str = ""
    risk_flags: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TradingAgent(ABC):
    def __init__(self, name: str, enabled: bool = False):
        self.name = name
        self.enabled = enabled

    @abstractmethod
    async def analyze(self, market_context: Dict[str, Any]) -> AgentAnalysisResult:
        """
        Analyzes provided market context (indicators, orderbook, news, etc.)
        and returns an AgentAnalysisResult.
        CANNOT execute orders directly.
        """
        pass
