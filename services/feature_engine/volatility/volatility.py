from typing import Tuple

import numpy as np
import pandas as pd


def calculate_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Calculates Average True Range (ATR)."""
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1.0 / period, adjust=False).mean()
    return atr


def calculate_bollinger_bands(
    series: pd.Series,
    period: int = 20,
    num_std: float = 2.0,
) -> Tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """Calculates Bollinger Bands: Upper, Middle, Lower, and Bandwidth."""
    middle = series.rolling(window=period).mean()
    std = series.rolling(window=period).std()
    upper = middle + (std * num_std)
    lower = middle - (std * num_std)
    bandwidth = (upper - lower) / (middle + 1e-9)
    return upper, middle, lower, bandwidth


def calculate_realized_volatility(close: pd.Series, window: int = 20) -> pd.Series:
    """Calculates annualized/standardized realized volatility from log returns."""
    log_returns = np.log(close / close.shift(1))
    return log_returns.rolling(window=window).std() * np.sqrt(365 * 24)
