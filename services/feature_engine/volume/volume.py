import pandas as pd


def calculate_volume_sma(volume: pd.Series, period: int = 20) -> pd.Series:
    """Calculates Simple Moving Average of Volume."""
    return volume.rolling(window=period).mean()


def calculate_volume_ratio(volume: pd.Series, period: int = 20) -> pd.Series:
    """Ratio of current volume against its historical average."""
    vol_sma = calculate_volume_sma(volume, period)
    return volume / (vol_sma + 1e-9)


def calculate_vwap(
    high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series
) -> pd.Series:
    """Calculates Volume Weighted Average Price (VWAP)."""
    typical_price = (high + low + close) / 3.0
    cum_pv = (typical_price * volume).cumsum()
    cum_vol = volume.cumsum()
    return cum_pv / (cum_vol + 1e-9)
