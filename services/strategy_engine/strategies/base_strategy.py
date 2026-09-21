from abc import ABC, abstractmethod
from typing import Any, Optional

import pandas as pd

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

    def evaluate_from_dataframe(
        self,
        df: pd.DataFrame,
        symbol: str,
    ) -> Optional[Signal]:
        """
        Default adapter converting a raw DataFrame into FeatureVector + MarketRegimeState,
        then delegating to self.evaluate(features, regime_state).
        """
        if not self.enabled or df is None or len(df) < 5:
            return None

        from services.feature_engine.features import FeatureEngine
        from services.regime_engine.detector import RegimeDetector
        from shared.enums import Timeframe

        tf = getattr(self, "timeframe", None) or Timeframe.M15
        fv = FeatureEngine.get_feature_vector_from_dataframe(df, symbol=symbol, timeframe=tf)
        if not fv:
            return None

        regime_detector = RegimeDetector()
        regime_state = regime_detector.detect(fv)

        return self.evaluate(fv, regime_state)

