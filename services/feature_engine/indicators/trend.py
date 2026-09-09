import pandas as pd


def calculate_ema(series: pd.Series, span: int) -> pd.Series:
    """Calculates Exponential Moving Average without look-ahead bias."""
    return series.ewm(span=span, adjust=False).mean()


def calculate_adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """
    Calculates Average Directional Index (ADX) measuring trend strength.
    Strictly causal (no future data).
    Correctly handles tied directional movement (+DM == -DM -> both zero per Wilder).
    """
    up_move = high.diff()
    down_move = -low.diff()

    # Wilder rule: if up_move == down_move or both <= 0, both directional movements are 0.0
    plus_dm = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    minus_dm = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    atr = tr.ewm(alpha=1.0 / period, adjust=False).mean()
    atr_safe = atr.where(atr > 1e-9, 1e-9)

    plus_di = 100 * (plus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr_safe)
    minus_di = 100 * (minus_dm.ewm(alpha=1.0 / period, adjust=False).mean() / atr_safe)

    di_sum = plus_di + minus_di
    di_diff = (plus_di - minus_di).abs()

    # If di_sum is 0 (e.g. perfect tie or flat movement), dx is 0.0
    dx = pd.Series(0.0, index=high.index)
    valid_mask = di_sum > 1e-9
    dx[valid_mask] = 100 * (di_diff[valid_mask] / di_sum[valid_mask])

    adx = dx.ewm(alpha=1.0 / period, adjust=False).mean()
    return adx
