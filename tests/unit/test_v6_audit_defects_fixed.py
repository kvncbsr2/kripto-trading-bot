import math
import pandas as pd
import pytest

from core.llm.llm_provider import HeuristicFallbackProvider
from services.execution.paper_execution import PaperExecutionEngine
from services.feature_engine.indicators.momentum import calculate_rsi
from services.feature_engine.indicators.trend import calculate_adx
from services.market_data.data_quality import DataQualityEngine, QualitySeverity
from services.risk_engine.position_sizing import calculate_atr_position_size
from shared.enums import SignalDirection
from shared.schemas import RiskDecision


def test_adx_tied_directional_movement_yields_zero():
    """
    V6 AUDIT DEFECT:
    In legacy V6, tied directional movement (+DM == -DM) incorrectly reported ~94.44
    due to in-place mutation of plus_dm before evaluating minus_dm.
    The corrected implementation must yield 0.0 directional movement and 0.0 ADX.
    """
    highs = [100.0]
    lows = [90.0]
    closes = [95.0]
    for i in range(1, 30):
        highs.append(highs[-1] + 5.0)
        lows.append(lows[-1] - 5.0)
        closes.append(closes[-1])

    s_high = pd.Series(highs)
    s_low = pd.Series(lows)
    s_close = pd.Series(closes)

    adx = calculate_adx(s_high, s_low, s_close, period=14)
    last_adx = float(adx.iloc[-1])
    assert last_adx == 0.0, f"Expected 0.0 for tied directional movement, got {last_adx}"


def test_rsi_constant_series_yields_neutral_50():
    """
    V6 AUDIT DEFECT:
    In legacy V6, constant price series reported RSI = 0.0 because rs = 0 / 1e-9 = 0.
    The neutral value for a series with zero gain and zero loss is 50.0.
    """
    constant_prices = pd.Series([100.0] * 30)
    rsi = calculate_rsi(constant_prices, period=14)
    last_rsi = float(rsi.iloc[-1])
    assert last_rsi == 50.0, f"Expected neutral 50.0 for constant series, got {last_rsi}"


def test_spread_validation_rejects_nan_and_inf():
    """
    V6 AUDIT DEFECT:
    In legacy V6, validate_spread accepted NaN bid/ask because comparisons with NaN
    evaluate to False.
    The corrected engine must trap NaN and Inf and return TRADING_LOCK with passed=False.
    """
    engine = DataQualityEngine()

    res_nan_bid = engine.validate_spread("BTC/USDT", float("nan"), 60000.0)
    assert res_nan_bid.passed is False
    assert res_nan_bid.severity == QualitySeverity.LOCK

    res_nan_ask = engine.validate_spread("BTC/USDT", 59990.0, float("nan"))
    assert res_nan_ask.passed is False
    assert res_nan_ask.severity == QualitySeverity.LOCK

    res_inf_ask = engine.validate_spread("BTC/USDT", 59990.0, float("inf"))
    assert res_inf_ask.passed is False
    assert res_inf_ask.severity == QualitySeverity.LOCK

    res_valid = engine.validate_spread("BTC/USDT", 59990.0, 60000.0)
    assert res_valid.passed is True
    assert res_valid.severity == QualitySeverity.OK


def test_atr_position_sizing_reports_actual_risk_when_exposure_capped():
    """
    V6 AUDIT DEFECT:
    In legacy V6, ATR sizing reported the full planned risk budget even after
    exposure caps scaled down the position size.
    The corrected implementation must scale down the reported risk amount to match
    the actual price-distance risk.
    """
    equity = 5000.0
    entry = 60000.0
    stop = 59000.0
    risk_per_trade = 0.005

    size_uncapped, risk_uncapped = calculate_atr_position_size(
        equity=equity,
        entry_price=entry,
        stop_price=stop,
        risk_per_trade=risk_per_trade,
        max_position_equity_ratio=0.50,
    )
    assert size_uncapped == 0.025
    assert risk_uncapped == 25.00

    size_capped, risk_capped = calculate_atr_position_size(
        equity=equity,
        entry_price=entry,
        stop_price=stop,
        risk_per_trade=risk_per_trade,
        max_position_equity_ratio=0.10,
    )
    assert size_capped == 0.008333
    assert risk_capped == 8.33, f"Expected risk $8.33, got ${risk_capped}"
    assert risk_capped < 25.00, "Capped position must not report uncapped risk budget"


def test_paper_execution_ambiguous_candle_prioritizes_stop_loss():
    """
    V6 AUDIT DEFECT:
    In legacy V6, paper execution checked take-profit before stop-loss when both
    were touched in one candle, producing optimistic ambiguous-bar results.
    The corrected implementation must fail-closed to Stop-Loss.
    """
    broker = PaperExecutionEngine(
        initial_balance=10000.0,
        enable_trailing_stop=False,
        enable_partial_exit=False,
    )

    decision = RiskDecision(
        symbol="BTC/USDT",
        approved=True,
        direction=SignalDirection.LONG,
        entry_price=100.0,
        stop_loss=90.0,
        take_profit=120.0,
        calculated_size=1.0,
        risk_amount=10.0,
    )
    broker.execute_market_order(decision)

    res = broker.check_position_stops_and_targets(
        symbol="BTC/USDT",
        high=125.0,
        low=85.0,
        close=95.0,
    )
    assert res is not None
    closed_pos, reason, exit_price = res

    assert reason == "STOP_LOSS", f"Expected STOP_LOSS on ambiguous candle, got {reason}"
    assert exit_price == 90.0


@pytest.mark.asyncio
async def test_heuristic_reflection_no_invented_macro_cause():
    """
    V6 AUDIT DEFECT:
    In legacy V6, the default heuristic reflection invented "Market reacted to macro level."
    when no market context existed.
    The corrected implementation must state factual context or UNKNOWN.
    """
    provider = HeuristicFallbackProvider()
    response = await provider.generate(
        system_prompt="Analyze trade reflection",
        user_prompt="Post-trade reflection for BTC/USDT",
        json_mode=True,
    )
    import json
    data = json.loads(response)
    root_cause = data.get("root_cause", "")
    assert "macro level" not in root_cause.lower(), (
        f"Heuristic reflection must not invent macro causes without context: {root_cause}"
    )
    assert "unknown" in root_cause.lower() or "insufficient" in root_cause.lower()
