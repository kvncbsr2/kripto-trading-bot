from typing import Tuple

import pandas as pd


def calculate_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """
    Calculates Relative Strength Index (RSI).
    Properly handles flat/constant price series by returning neutral 50.0.
    """
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)

    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()

    both_zero = (avg_gain == 0.0) & (avg_loss == 0.0)
    zero_loss = (avg_loss == 0.0) & (avg_gain > 0.0)
    zero_gain = (avg_gain == 0.0) & (avg_loss > 0.0)

    rs = avg_gain / (avg_loss + 1e-12)
    rsi = 100.0 - (100.0 / (1.0 + rs))

    # Neutral 50 for constant price series
    rsi = rsi.where(~both_zero, 50.0)
    # Pure gains -> 100.0
    rsi = rsi.where(~zero_loss, 100.0)
    # Pure losses -> 0.0
    rsi = rsi.where(~zero_gain, 0.0)
    return rsi


def calculate_macd(
    series: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Calculates MACD Line, Signal Line, and MACD Histogram."""
    fast_ema = series.ewm(span=fast, adjust=False).mean()
    slow_ema = series.ewm(span=slow, adjust=False).mean()
    macd_line = fast_ema - slow_ema
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram = macd_line - signal_line
    return macd_line, signal_line, histogram


def calculate_roc(series: pd.Series, period: int = 12) -> pd.Series:
    """Calculates Rate of Change (ROC)."""
    return ((series - series.shift(period)) / (series.shift(period) + 1e-9)) * 100.0
