"""
Authoritative Strategy Registry for KRIPTO AGENT.
Decouples Strategy selection from Risk Profiles (Requirement: STRATEGY != RISK PROFILE).
"""

from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, List, Optional

from services.strategy_engine.strategies.base_strategy import BaseStrategy
from services.strategy_engine.strategies.r10_rsi_divergence import (
    R10RSIDivergenceStrategy,
    create_r10_strategy_from_settings,
)
from services.strategy_engine.strategies.regime_gated_pullback import RegimeGatedPullbackStrategy
from services.strategy_engine.strategies.mean_reversion import MeanReversionStrategy
from services.strategy_engine.strategies.trend_following import TrendFollowingStrategy
from services.strategy_engine.strategies.rsi_divergence import RSIDivergenceStrategy
from shared.logging import get_logger

logger = get_logger("strategy-registry", service="strategy_engine")

DEFAULT_STRATEGY_ID = "r10_rsi_divergence"


@dataclass(frozen=True)
class StrategyMetadata:
    id: str
    name: str
    description: str
    category: str
    default_timeframe: str
    enabled: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _build_r10_strategy(**kwargs) -> BaseStrategy:
    left_bars = kwargs.get("left_bars", 5)
    right_bars = kwargs.get("right_bars", 2)
    min_signal_score = kwargs.get("min_signal_score", 65.0)
    risk_reward_ratio = kwargs.get("risk_reward_ratio", 2.0)
    timeframe = kwargs.get("timeframe", "15m")
    return R10RSIDivergenceStrategy(
        left_bars=left_bars,
        right_bars=right_bars,
        min_signal_score=min_signal_score,
        risk_reward_ratio=risk_reward_ratio,
        timeframe=timeframe,
    )


def _build_pullback_strategy(**kwargs) -> BaseStrategy:
    min_signal_score = kwargs.get("min_signal_score", 60.0)
    risk_reward_ratio = kwargs.get("risk_reward_ratio", 2.0)
    return RegimeGatedPullbackStrategy(
        risk_reward_ratio=risk_reward_ratio,
    )


def _build_mean_reversion_strategy(**kwargs) -> BaseStrategy:
    return MeanReversionStrategy()


def _build_trend_following_strategy(**kwargs) -> BaseStrategy:
    return TrendFollowingStrategy()


def _build_raw_rsi_strategy(**kwargs) -> BaseStrategy:
    return RSIDivergenceStrategy()


STRATEGY_REGISTRY: Dict[str, StrategyMetadata] = {
    "r10_rsi_divergence": StrategyMetadata(
        id="r10_rsi_divergence",
        name="R10 RSI Uyumsuzluğu (Dip Dönüşü)",
        description="Çift pivot onaylı ve EMA9 geri kazanımlı nedensel RSI pozitif uyumsuzluk dip dönüş stratejisi.",
        category="SWING_DIVERGENCE",
        default_timeframe="15m",
        enabled=True,
    ),
    "regime_gated_pullback": StrategyMetadata(
        id="regime_gated_pullback",
        name="Trend İçi Geri Çekilme (Pullback)",
        description="1H EMA trend rejimi hizalamalı ve 15M VWAP/EMA20 tepkili yükseliş trendi içi geri çekilme stratejisi.",
        category="TREND_PULLBACK",
        default_timeframe="15m",
        enabled=True,
    ),
    "mean_reversion": StrategyMetadata(
        id="mean_reversion",
        name="Ortalamaya Dönüş (Aşırı Satım Tepkisi)",
        description="Yatay veya dalgalı piyasada Bollinger alt bandı ve aşırı satım sıçramalarını yakalayan osilasyon stratejisi.",
        category="STATISTICAL_REVERSION",
        default_timeframe="15m",
        enabled=True,
    ),
    "trend_following": StrategyMetadata(
        id="trend_following",
        name="Trend Takipçisi (Kırılım & Momentum)",
        description="Çoklu zaman dilimli EMA ve MACD momentum devamı stratejisi.",
        category="TREND_MOMENTUM",
        default_timeframe="1h",
        enabled=True,
    ),
    "rsi_divergence": StrategyMetadata(
        id="rsi_divergence",
        name="Klasik RSI Uyumsuzluğu",
        description="Klasik filtresiz RSI uyumsuzluk tespit stratejisi (Referans / Kıyaslama).",
        category="BENCHMARK",
        default_timeframe="15m",
        enabled=True,
    ),
}

_STRATEGY_BUILDERS: Dict[str, Callable[..., BaseStrategy]] = {
    "r10_rsi_divergence": _build_r10_strategy,
    "regime_gated_pullback": _build_pullback_strategy,
    "mean_reversion": _build_mean_reversion_strategy,
    "trend_following": _build_trend_following_strategy,
    "rsi_divergence": _build_raw_rsi_strategy,
}


def get_all_strategies() -> List[Dict[str, Any]]:
    return [meta.to_dict() for meta in STRATEGY_REGISTRY.values()]


def get_strategy_metadata(strategy_id: str) -> StrategyMetadata:
    if strategy_id not in STRATEGY_REGISTRY:
        logger.warning(f"Unknown strategy_id '{strategy_id}', defaulting to '{DEFAULT_STRATEGY_ID}'.")
        return STRATEGY_REGISTRY[DEFAULT_STRATEGY_ID]
    return STRATEGY_REGISTRY[strategy_id]


def create_strategy(strategy_id: str, **kwargs) -> BaseStrategy:
    sid = strategy_id if strategy_id in _STRATEGY_BUILDERS else DEFAULT_STRATEGY_ID
    builder = _STRATEGY_BUILDERS[sid]
    return builder(**kwargs)
