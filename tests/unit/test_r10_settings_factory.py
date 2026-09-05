from services.strategy_engine.strategies.r10_rsi_divergence import (
    create_r10_strategy_from_settings,
)
from services.strategy_engine.strategy_manager import StrategyManager
from shared.config import get_settings


def test_r10_factory_uses_settings():
    settings = get_settings()
    strategy = create_r10_strategy_from_settings()
    assert strategy.rsi_length == settings.R10_RSI_LENGTH
    assert strategy.left_bars == settings.R10_PIVOT_LEFT
    assert strategy.right_bars == settings.R10_PIVOT_RIGHT
    assert strategy.atr_multiplier == settings.ATR_SL_MULTIPLIER
    assert strategy.risk_reward_ratio == settings.PREFERRED_RISK_REWARD
    assert strategy.min_signal_score == settings.MIN_SIGNAL_SCORE
    assert strategy.timeframe == settings.R10_TIMEFRAME


def test_strategy_manager_contains_same_canonical_r10_configuration():
    settings = get_settings()
    manager = StrategyManager()
    r10 = next(s for s in manager.strategies if s.name == "r10_rsi_divergence")
    assert r10.rsi_length == settings.R10_RSI_LENGTH
    assert r10.left_bars == settings.R10_PIVOT_LEFT
    assert r10.right_bars == settings.R10_PIVOT_RIGHT
    assert r10.timeframe == settings.R10_TIMEFRAME


def test_r10_score_mapping_is_monotonic_and_bounded():
    from services.strategy_engine.strategies.r10_rsi_divergence import R10RSIDivergenceStrategy

    scores = [R10RSIDivergenceStrategy.calculate_signal_score_from_divergence(v) for v in (0, 10, 14, 20, 30)]
    assert scores == sorted(scores)
    assert scores[0] == 50.0
    assert scores[2] == 70.0
    assert scores[-1] == 100.0
