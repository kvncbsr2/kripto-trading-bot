import pandas as pd

from services.feature_engine.indicators.divergence import RSIDivergenceDetector
from services.feature_engine.indicators.momentum import calculate_macd, calculate_rsi
from services.feature_engine.indicators.trend import calculate_ema
from services.feature_engine.volatility.volatility import calculate_atr, calculate_bollinger_bands


def test_ema_calculation():
    series = pd.Series([10.0, 11.0, 12.0, 13.0, 14.0, 15.0])
    ema = calculate_ema(series, span=3)
    assert len(ema) == len(series)
    assert ema.iloc[-1] > ema.iloc[0]
    # No NaN in later values
    assert not pd.isna(ema.iloc[-1])


def test_rsi_calculation():
    # Strong upward trend -> RSI should be high (> 70)
    series = pd.Series([float(i) for i in range(1, 30)])
    rsi = calculate_rsi(series, period=14)
    assert rsi.iloc[-1] > 70.0
    assert 0.0 <= rsi.iloc[-1] <= 100.0


def test_macd_calculation():
    series = pd.Series([float(i) for i in range(1, 40)])
    line, signal, hist = calculate_macd(series)
    assert len(line) == len(series)
    assert not pd.isna(line.iloc[-1])


def test_atr_and_bollinger():
    high = pd.Series([102.0, 103.0, 105.0, 104.0, 106.0] * 5)
    low = pd.Series([98.0, 99.0, 100.0, 101.0, 102.0] * 5)
    close = pd.Series([100.0, 101.0, 104.0, 102.0, 105.0] * 5)

    atr = calculate_atr(high, low, close, period=5)
    assert atr.iloc[-1] > 0

    upper, mid, lower, width = calculate_bollinger_bands(close, period=5)
    assert upper.iloc[-1] > mid.iloc[-1] > lower.iloc[-1]
    assert width.iloc[-1] > 0


def test_rsi_divergence_detector():
    # Create deliberate bullish divergence: Price Lower Low, RSI Higher Low
    close = pd.Series([100.0, 95.0, 90.0, 92.0, 88.0, 85.0, 89.0, 87.0, 84.0, 90.0] * 4)
    low = close - 2.0
    high = close + 2.0
    rsi = pd.Series([25.0, 22.0, 20.0, 28.0, 30.0, 32.0, 35.0, 34.0, 38.0, 42.0] * 4)

    div = RSIDivergenceDetector.detect_divergence(low, high, close, rsi, window=2, lookback=25)
    assert "bullish_divergence" in div
    assert "bearish_divergence" in div
