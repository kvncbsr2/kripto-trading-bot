from typing import List, Optional

from services.signal_engine.scorer import SignalScorer
from services.strategy_engine.strategies.base_strategy import BaseStrategy
from services.strategy_engine.strategies.mean_reversion import MeanReversionStrategy
from services.strategy_engine.strategies.r10_rsi_divergence import R10RSIDivergenceStrategy
from services.strategy_engine.strategies.rsi_divergence import RSIDivergenceStrategy
from services.strategy_engine.strategies.trend_following import TrendFollowingStrategy
from shared.logging import get_logger
from shared.schemas import FeatureVector, MarketRegimeState, Signal

logger = get_logger("strategy-manager", service="strategy_engine")


class StrategyManager:
    def __init__(self, strategies: Optional[List[BaseStrategy]] = None):
        if strategies is not None:
            self.strategies = strategies
        else:
            self.strategies = [
                TrendFollowingStrategy(),
                MeanReversionStrategy(),
                RSIDivergenceStrategy(),
                R10RSIDivergenceStrategy(),
            ]

    def register_strategy(self, strategy: BaseStrategy):
        self.strategies.append(strategy)

    def evaluate(
        self,
        features: FeatureVector,
        regime_state: MarketRegimeState,
    ) -> List[Signal]:
        signals: List[Signal] = []
        opp_score, opp_label = SignalScorer.calculate_opportunity_score(features, regime_state)

        for strat in self.strategies:
            if not strat.enabled:
                continue
            try:
                sig = strat.evaluate(features, regime_state)
                if sig:
                    # Enrich with opportunity score
                    sig.metadata["opportunity_score"] = opp_score
                    sig.metadata["opportunity_label"] = opp_label

                    # If confidence not set by strategy, score it
                    if not sig.metadata.get("score"):
                        score, category, subscores = SignalScorer.score_signal(
                            features, regime_state, sig.direction, has_divergence=False
                        )
                        sig.metadata["score"] = score
                        sig.metadata["category"] = category
                        sig.metadata["subscores"] = subscores
                        sig.confidence = round(score / 100.0, 2)

                    logger.info(
                        f"Signal generated: [{strat.name}] {sig.symbol} {sig.direction.value} "
                        f"Score={sig.metadata.get('score')}/100 Opp={opp_score}/100",
                        extra={"symbol": sig.symbol, "strategy": strat.name},
                    )
                    signals.append(sig)
            except Exception as e:
                logger.error(f"Error evaluating strategy {strat.name}: {e}")
        return signals
