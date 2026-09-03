from typing import Tuple


def calculate_atr_position_size(
    equity: float,
    entry_price: float,
    stop_price: float,
    risk_per_trade: float = 0.005,  # 0.5%
    min_size: float = 0.0001,
    max_position_equity_ratio: float = 0.25,  # Max 25% of total capital in single trade
) -> Tuple[float, float]:
    """
    Calculates size based on ATR stop distance:
    risk_amount = equity * risk_per_trade
    stop_distance = abs(entry_price - stop_price)
    position_size = risk_amount / stop_distance
    Returns (position_size, risk_amount)
    """
    stop_distance = abs(entry_price - stop_price)
    if stop_distance <= 0 or entry_price <= 0:
        return 0.0, 0.0

    risk_amount = equity * risk_per_trade
    raw_size = risk_amount / stop_distance

    # Guard against max capital exposure
    max_allowed_size = (equity * max_position_equity_ratio) / entry_price
    final_size = min(raw_size, max_allowed_size)

    if final_size < min_size:
        return 0.0, 0.0

    return round(final_size, 6), round(risk_amount, 2)
