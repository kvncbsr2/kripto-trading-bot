"""
Authoritative Strategy Registry for KRIPTO AGENT.
Decouples Strategy selection from Risk Profiles (Requirement: STRATEGY != RISK PROFILE).
"""

from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, List, Optional

from services.strategy_engine.strategies.base_strategy import BaseStrategy
from services.strategy_engine.strategies.momentum_dip_rebound import MomentumDipReboundStrategy
from services.strategy_engine.strategies.turbo_fast_strike import TurboFastStrikeStrategy
from services.strategy_engine.strategies.r10_rsi_divergence import (
    R10RSIDivergenceStrategy,
    create_r10_strategy_from_settings,
)
from services.strategy_engine.strategies.regime_gated_pullback import RegimeGatedPullbackStrategy
from services.strategy_engine.strategies.mean_reversion import MeanReversionStrategy
from services.strategy_engine.strategies.trend_following import TrendFollowingStrategy
from services.strategy_engine.strategies.rsi_divergence import RSIDivergenceStrategy
from services.strategy_engine.strategies.bollinger_volume_breakout import BollingerVolumeBreakoutStrategy
from services.strategy_engine.strategies.ema_macd_pullback import EmaMacdPullbackStrategy
from shared.logging import get_logger

logger = get_logger("strategy-registry", service="strategy_engine")

DEFAULT_STRATEGY_ID = "momentum_dip_rebound"


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


def _build_momentum_dip_rebound_strategy(**kwargs) -> BaseStrategy:
    min_signal_score = kwargs.get("min_signal_score", 65.0)
    risk_reward_ratio = kwargs.get("risk_reward_ratio", 2.0)
    timeframe = kwargs.get("timeframe", "15m")
    return MomentumDipReboundStrategy(
        min_signal_score=min_signal_score,
        risk_reward_ratio=risk_reward_ratio,
        timeframe=timeframe,
    )


def _build_turbo_strategy(**kwargs) -> BaseStrategy:
    min_signal_score = kwargs.get("min_signal_score", 55.0)
    risk_reward_ratio = kwargs.get("risk_reward_ratio", 2.0)
    timeframe = kwargs.get("timeframe", "5m")
    return TurboFastStrikeStrategy(
        min_signal_score=min_signal_score,
        risk_reward_ratio=risk_reward_ratio,
        timeframe=timeframe,
    )


def _build_bollinger_breakout_strategy(**kwargs) -> BaseStrategy:
    min_signal_score = kwargs.get("min_signal_score", 65.0)
    rr_ratio = kwargs.get("risk_reward_ratio", 1.5)
    timeframe = kwargs.get("timeframe", "15m")
    bb_period = kwargs.get("bb_period", 30)
    vol_mult = kwargs.get("vol_mult", 1.2)
    atr_sl_mult = kwargs.get("atr_sl_mult", 2.2)
    return BollingerVolumeBreakoutStrategy(
        min_signal_score=min_signal_score,
        rr_ratio=rr_ratio,
        timeframe=timeframe,
        bb_period=bb_period,
        vol_mult=vol_mult,
        atr_sl_mult=atr_sl_mult,
    )


def _build_ema_macd_pullback_strategy(**kwargs) -> BaseStrategy:
    min_signal_score = kwargs.get("min_signal_score", 65.0)
    rr_ratio = kwargs.get("risk_reward_ratio", 1.5)
    timeframe = kwargs.get("timeframe", "15m")
    fast_ema = kwargs.get("fast_ema", 21)
    slow_ema = kwargs.get("slow_ema", 100)
    atr_sl_mult = kwargs.get("atr_sl_mult", 1.5)
    return EmaMacdPullbackStrategy(
        min_signal_score=min_signal_score,
        rr_ratio=rr_ratio,
        timeframe=timeframe,
        fast_ema=fast_ema,
        slow_ema=slow_ema,
        atr_sl_mult=atr_sl_mult,
    )


from enum import Enum


class StrategyValidationStatus(str, Enum):
    RESEARCH_ONLY = "RESEARCH_ONLY"
    PAPER_VALIDATION = "PAPER_VALIDATION"
    VALIDATED = "VALIDATED"


def compute_strategy_validation_status(metrics: Dict[str, Any]) -> StrategyValidationStatus:
    """
    Computes rigorous programmatic validation status according to Item 6 invariants:
    - test/OOS trade count >= 30
    - OOS Profit Factor > 1.30
    - Bootstrap 95% PF confidence interval lower bound > 1.0
    - Max Drawdown < 15.0%
    - Positive expectancy (validation and test)
    - Cost stress passed
    - Multi-regime acceptance
    """
    total_trades = metrics.get("total_trades") if metrics.get("total_trades") is not None else metrics.get("trade_count", 0)
    pf = metrics.get("profit_factor", 0.0)
    ci_low = metrics.get("bootstrap_pf_ci_low") if metrics.get("bootstrap_pf_ci_low") is not None else metrics.get("ci_95_low", 0.0)

    max_dd = metrics.get("max_drawdown_pct")
    if max_dd is None:
        raw_dd = metrics.get("max_drawdown", 100.0)
        max_dd = raw_dd * 100.0 if raw_dd <= 1.0 else raw_dd

    expectancy = metrics.get("expectancy_pct")
    if expectancy is None:
        expectancy = metrics.get("expectancy", -1.0)

    cost_stress = bool(metrics.get("cost_stress_passed", False))

    multi_regime = metrics.get("multi_regime_passed")
    if multi_regime is None:
        regimes = metrics.get("regimes_tested", [])
        multi_regime = len(regimes) >= 2

    if (
        total_trades >= 30
        and pf > 1.30
        and ci_low > 1.0
        and max_dd < 15.0
        and expectancy > 0.0
        and cost_stress
        and multi_regime
    ):
        return StrategyValidationStatus.VALIDATED
    elif total_trades > 0:
        return StrategyValidationStatus.PAPER_VALIDATION
    return StrategyValidationStatus.RESEARCH_ONLY


STRATEGY_REGISTRY: Dict[str, StrategyMetadata] = {
    "bollinger_volume_breakout": StrategyMetadata(
        id="bollinger_volume_breakout",
        name="Bollinger Hacim Kırılımı (Karantinada — RESEARCH_ONLY / Negatif Performans)",
        description="Son 4 işlemde -52.59 USDT kayıp üretmesi nedeniyle canlı/paper alımdan çıkarılmış, araştırma ve optimizasyon karantinasına alınmıştır.",
        category="QUANT_VOLATILITY_BREAKOUT",
        default_timeframe="15m",
        enabled=False,
    ),
    "ema_macd_pullback": StrategyMetadata(
        id="ema_macd_pullback",
        name="EMA MACD Trend Düzeltme (Pullback — Araştırma Adayı)",
        description="50/100 EMA ana trendi yukarıyken MACD histogram toparlanması ile trend yönlü giriş arayan kantitatif model.",
        category="QUANT_TREND_PULLBACK",
        default_timeframe="15m",
        enabled=True,
    ),
    "turbo_fast_strike": StrategyMetadata(
        id="turbo_fast_strike",
        name="Turbo Hızlı Vur-Kaç (Scalp Modu — Araştırma Adayı)",
        description="5m periyodunda yüksek beta oynak coinlerde dip sıçraması ve momentum takibi yapan scalping modeli.",
        category="HIGH_FREQUENCY_MOMENTUM",
        default_timeframe="5m",
        enabled=True,
    ),
    "momentum_dip_rebound": StrategyMetadata(
        id="momentum_dip_rebound",
        name="Dinamik Momentum ve Dip Dönüşü (Araştırma Adayı — Paper Trading Doğrulaması Devam Ediyor)",
        description="Momentum kırılımı ve aşırı satım sıçramasını birleştiren kantitatif dip dönüş modeli. Canlı paper trading izlemesindedir.",
        category="MULTI_FACTOR_MOMENTUM_DIP",
        default_timeframe="15m",
        enabled=True,
    ),
    "r10_rsi_divergence": StrategyMetadata(
        id="r10_rsi_divergence",
        name="R10 RSI Uyumsuzluğu (Dip Dönüşü — Araştırma Adayı)",
        description="Çift pivot onaylı ve EMA9 geri kazanımlı nedensel RSI pozitif uyumsuzluk dip dönüş stratejisi.",
        category="SWING_DIVERGENCE",
        default_timeframe="15m",
        enabled=True,
    ),
    "regime_gated_pullback": StrategyMetadata(
        id="regime_gated_pullback",
        name="Trend İçi Geri Çekilme (Pullback — Araştırma Adayı)",
        description="1H EMA trend rejimi hizalamalı ve 15M VWAP/EMA20 tepkili yükseliş trendi içi geri çekilme stratejisi.",
        category="TREND_PULLBACK",
        default_timeframe="15m",
        enabled=True,
    ),
    "mean_reversion": StrategyMetadata(
        id="mean_reversion",
        name="Ortalamaya Dönüş (Aşırı Satım Tepkisi — Araştırma Adayı)",
        description="Yatay veya dalgalı piyasada Bollinger alt bandı ve aşırı satım sıçramalarını yakalayan osilasyon stratejisi.",
        category="STATISTICAL_REVERSION",
        default_timeframe="15m",
        enabled=True,
    ),
    "trend_following": StrategyMetadata(
        id="trend_following",
        name="Trend Takipçisi (Kırılım & Momentum — Araştırma Adayı)",
        description="Çoklu zaman dilimli EMA ve MACD momentum devamı stratejisi.",
        category="TREND_MOMENTUM",
        default_timeframe="1h",
        enabled=True,
    ),
    "rsi_divergence": StrategyMetadata(
        id="rsi_divergence",
        name="Klasik RSI Uyumsuzluğu (Referans / Kıyaslama)",
        description="Klasik filtresiz RSI uyumsuzluk tespit stratejisi (Referans / Kıyaslama).",
        category="BENCHMARK",
        default_timeframe="15m",
        enabled=True,
    ),
}

_STRATEGY_BUILDERS: Dict[str, Callable[..., BaseStrategy]] = {
    "bollinger_volume_breakout": _build_bollinger_breakout_strategy,
    "ema_macd_pullback": _build_ema_macd_pullback_strategy,
    "turbo_fast_strike": _build_turbo_strategy,
    "r10_rsi_divergence": _build_r10_strategy,
    "momentum_dip_rebound": _build_momentum_dip_rebound_strategy,
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


get_strategy = create_strategy
