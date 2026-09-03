from abc import ABC, abstractmethod
from typing import Optional

from shared.schemas import FeatureVector, MarketRegimeState, Signal


class BaseStrategy(ABC):
    def __init__(self, name: str, enabled: bool = True):
        self.name = name
        self.enabled = enabled

    @abstractmethod
    def evaluate(
        self,
        features: FeatureVector,
        regime_state: MarketRegimeState,
    ) -> Optional[Signal]:
        """
        Evaluates current feature vector and regime state.
        Returns a Signal object if criteria met, else None.
        """
        pass
