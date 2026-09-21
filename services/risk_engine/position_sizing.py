import math
from typing import Tuple


def calculate_atr_position_size(
    equity: float,
    entry_price: float,
    stop_price: float,
    risk_per_trade: float = 0.005,  # 0.5%
    min_size: float = 0.0001,
    max_position_equity_ratio: float = 0.40,  # Max 40% of total capital in single trade
    fee_rate: float = 0.0,
    slippage_bps: float = 0.0,
) -> Tuple[float, float]:
    """
    Calculates size based on ATR stop distance and total planned risk budget.
    Accounts for transaction fees and adverse slippage in unit risk.
    When exposure caps reduce position size, reported planned risk reflects
    actual price-distance risk (final_size * unit_risk), preventing false risk telemetry.

    Returns (position_size, actual_risk_amount)
    """
    values = (equity, entry_price, stop_price, risk_per_trade, min_size,
              max_position_equity_ratio, fee_rate, slippage_bps)
    if not all(math.isfinite(v) for v in values):
        return 0.0, 0.0
    if stop_price <= 0 or risk_per_trade <= 0 or min_size <= 0:
        return 0.0, 0.0
    if not 0 < max_position_equity_ratio <= 1 or fee_rate < 0 or slippage_bps < 0:
        return 0.0, 0.0
    stop_distance = abs(entry_price - stop_price)
    if stop_distance <= 0 or entry_price <= 0 or equity <= 0:
        return 0.0, 0.0

    slippage_rate = slippage_bps / 10000.0
    friction_per_unit = (entry_price + stop_price) * (
        fee_rate + slippage_rate + fee_rate * slippage_rate
    )
    unit_risk = stop_distance + friction_per_unit

    if unit_risk <= 0:
        return 0.0, 0.0

    target_risk_amount = equity * risk_per_trade
    raw_size = target_risk_amount / unit_risk

    # Guard against max capital exposure
    max_allowed_size = (equity * max_position_equity_ratio) / entry_price
    # Round down so quantity precision cannot increase the approved risk budget.
    final_size = math.floor(min(raw_size, max_allowed_size) * 1_000_000) / 1_000_000

    if final_size < min_size:
        return 0.0, 0.0

    # Actual price-distance risk (+ friction) based on final allocated size
    actual_risk = final_size * unit_risk

    return round(final_size, 6), round(actual_risk, 2)


def calculate_kelly_fraction(
    win_rate: float,
    win_loss_ratio: float,
    multiplier: float = 0.5,
    max_cap: float = 0.05,
) -> float:
    """
    Calculates Kelly Criterion optimal risk fraction:
      f* = (p * b - (1 - p)) / b
    Where:
      p = win_rate (0.0 to 1.0)
      b = win_loss_ratio (avg_win / avg_loss)
      multiplier = fraction of Kelly to use (0.5 for Half-Kelly, 0.25 for Quarter-Kelly)
      max_cap = ceiling on single trade risk (e.g., 0.05 = 5% max)
    Returns:
      Safe risk fraction (e.g., 0.025 = 2.5%)
    """
    if win_loss_ratio <= 0 or win_rate <= 0:
        return 0.0
    p = win_rate
    q = 1.0 - p
    b = win_loss_ratio
    full_kelly = (p * b - q) / b
    if full_kelly <= 0:
        return 0.0
    fractional_kelly = full_kelly * multiplier
    return round(min(fractional_kelly, max_cap), 4)


def calculate_risk_of_ruin(
    win_rate: float,
    win_loss_ratio: float,
    risk_per_trade: float = 0.02,
    ruin_drawdown_pct: float = 0.50,
) -> float:
    """
    Perry Kaufman's Risk of Ruin (RoR) formula for systematic quantitative trading:
      RoR = ((1 - A) / (1 + A))^U
    where:
      A = (p * b - (1 - p)) / (p * b + (1 - p))  [Payoff-adjusted edge]
      U = ruin_drawdown_pct / risk_per_trade       [Number of risk units to ruin]
    Returns probability of ruin between 0.0 and 1.0 (0.01 = 1%).
    """
    if win_rate <= 0 or win_loss_ratio <= 0 or risk_per_trade <= 0:
        return 1.0
    p = win_rate
    q = 1.0 - p
    b = win_loss_ratio

    edge = (p * b) - q
    if edge <= 0:
        return 1.0  # Guaranteed ruin over infinite horizon if negative edge

    denom = (p * b) + q
    if denom <= 0:
        return 1.0
    a = edge / denom
    if a >= 1.0:
        return 0.0

    units = ruin_drawdown_pct / risk_per_trade
    ratio = (1.0 - a) / (1.0 + a)
    ror = math.pow(ratio, units)
    return round(min(1.0, max(0.0, ror)), 6)

