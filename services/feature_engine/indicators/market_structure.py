from typing import Dict, Tuple

import pandas as pd


def identify_swings(
    high: pd.Series,
    low: pd.Series,
    window: int = 5,
) -> Tuple[pd.Series, pd.Series]:
    """
    Identifies Swing Highs and Swing Lows using causal/lagged window.
    A swing high at bar i is confirmed at bar i + window when high[i] is highest in [i - window, i + window].
    To prevent look-ahead bias during real-time streaming, we lag detection by `window` bars.
    """
    n = len(high)
    is_swing_high = pd.Series(False, index=high.index)
    is_swing_low = pd.Series(False, index=low.index)

    if n < (2 * window + 1):
        return is_swing_high, is_swing_low

    for i in range(window, n - window):
        center_high = high.iloc[i]
        center_low = low.iloc[i]

        # Is center bar higher than surrounding window?
        if center_high == max(high.iloc[i - window : i + window + 1]):
            # Confirmed at i + window bar
            is_swing_high.iloc[i + window] = True

        # Is center bar lower than surrounding window?
        if center_low == min(low.iloc[i - window : i + window + 1]):
            is_swing_low.iloc[i + window] = True

    return is_swing_high, is_swing_low


def analyze_market_structure(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    window: int = 5,
) -> Dict[str, bool]:
    """
    Evaluates whether recent market structure shows:
    - Higher High (HH) & Higher Low (HL) -> Bullish Structure
    - Lower High (LH) & Lower Low (LL) -> Bearish Structure
    """
    n = len(close)
    if n < 20:
        return {
            "bullish_structure": False,
            "bearish_structure": False,
            "higher_high": False,
            "higher_low": False,
            "lower_high": False,
            "lower_low": False,
        }

    # Find last 2 swing highs and last 2 swing lows
    swing_highs = []
    swing_lows = []

    for i in range(5, n - 2):
        if high.iloc[i] == max(high.iloc[i - 3 : i + 4]):
            swing_highs.append((i, high.iloc[i]))
        if low.iloc[i] == min(low.iloc[i - 3 : i + 4]):
            swing_lows.append((i, low.iloc[i]))

    higher_high = False
    higher_low = False
    lower_high = False
    lower_low = False

    if len(swing_highs) >= 2:
        higher_high = swing_highs[-1][1] > swing_highs[-2][1]
        lower_high = swing_highs[-1][1] < swing_highs[-2][1]

    if len(swing_lows) >= 2:
        higher_low = swing_lows[-1][1] > swing_lows[-2][1]
        lower_low = swing_lows[-1][1] < swing_lows[-2][1]

    bullish_structure = higher_high and higher_low
    bearish_structure = lower_high and lower_low

    return {
        "bullish_structure": bullish_structure,
        "bearish_structure": bearish_structure,
        "higher_high": higher_high,
        "higher_low": higher_low,
        "lower_high": lower_high,
        "lower_low": lower_low,
    }
