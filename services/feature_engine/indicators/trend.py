import pandas as pd


def calculate_ema(series: pd.Series, span: int) -> pd.Series:
    """Calculates Exponential Moving Average without look-ahead bias."""
    return series.ewm(span=span, adjust=False).mean()


def calculate_adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """
    Calculates Average Directional Index (ADX) measuring trend strength.
    Strictly causal (no future data).
    """
    plus_dm = high.diff()
    minus_dm = -low.diff()

    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0.0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0.0)

    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr = tr.ewm(alpha=1.0 / period, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / (atr + 1e-9))
    minus_di = 100 * (minus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / (atr + 1e-9))

    dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di + 1e-9))
    adx = dx.ewm(alpha=1.0 / period, adjust=False).mean()
    return adx
