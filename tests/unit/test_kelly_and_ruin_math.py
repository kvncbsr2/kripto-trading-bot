"""
Unit tests for Institutional Quant Mathematics:
1. Kelly Criterion & Fractional Kelly (Ed Thorp / John Kelly Jr.)
2. Risk of Ruin (Perry Kaufman formula)
3. Expectancy Engine Kelly integration
"""

import pytest
from services.risk_engine.position_sizing import (
    calculate_kelly_fraction,
    calculate_risk_of_ruin,
)
from services.risk_engine.expectancy_engine import ExpectancyEngine


def test_kelly_fraction_basic():
    """
    Standard textbook Kelly test:
    Win rate p = 0.60, Win/Loss ratio b = 1.0 (even odds).
    f* = (p*b - q)/b = (0.60*1 - 0.40)/1 = 0.20 (20%).
    Half-Kelly (0.5x) = 0.10, Quarter-Kelly (0.25x) = 0.05.
    """
    full = calculate_kelly_fraction(win_rate=0.60, win_loss_ratio=1.0, multiplier=1.0, max_cap=1.0)
    assert full == 0.20

    half = calculate_kelly_fraction(win_rate=0.60, win_loss_ratio=1.0, multiplier=0.5, max_cap=1.0)
    assert half == 0.10

    quarter = calculate_kelly_fraction(win_rate=0.60, win_loss_ratio=1.0, multiplier=0.25, max_cap=1.0)
    assert quarter == 0.05


def test_kelly_asymmetric_payoff():
    """
    Asymmetric trend-following / scalping test:
    Win rate p = 0.40 (40%), Win/Loss ratio b = 2.5.
    f* = (0.40 * 2.5 - 0.60) / 2.5 = (1.0 - 0.60) / 2.5 = 0.40 / 2.5 = 0.16 (16%).
    Half-Kelly = 0.08 (capped at 0.05 if default cap applies).
    """
    full = calculate_kelly_fraction(win_rate=0.40, win_loss_ratio=2.5, multiplier=1.0, max_cap=1.0)
    assert full == 0.16

    capped_half = calculate_kelly_fraction(win_rate=0.40, win_loss_ratio=2.5, multiplier=0.5, max_cap=0.05)
    assert capped_half == 0.05


def test_kelly_negative_expectancy_zero():
    """Negative or zero expectancy should yield 0 risk fraction."""
    # p = 0.30, b = 1.0 -> f* = (0.3 - 0.7)/1.0 = -0.40 -> 0.0
    f = calculate_kelly_fraction(win_rate=0.30, win_loss_ratio=1.0)
    assert f == 0.0

    # Boundary conditions
    assert calculate_kelly_fraction(win_rate=0.0, win_loss_ratio=2.0) == 0.0
    assert calculate_kelly_fraction(win_rate=0.5, win_loss_ratio=0.0) == 0.0


def test_risk_of_ruin_calculation():
    """
    Risk of Ruin test:
    With strong positive edge (p=0.55, b=1.8), risk=2%, 40% drawdown threshold:
    Risk of Ruin should be practically negligible (< 0.001 or 0.1%).
    """
    ror = calculate_risk_of_ruin(win_rate=0.55, win_loss_ratio=1.8, risk_per_trade=0.02, ruin_drawdown_pct=0.40)
    assert 0.0 <= ror < 0.005  # Less than 0.5% chance of ruin

    # When edge is negative (p=0.35, b=1.0), ruin is guaranteed (1.0)
    bad_ror = calculate_risk_of_ruin(win_rate=0.35, win_loss_ratio=1.0, risk_per_trade=0.02)
    assert bad_ror == 1.0


def test_expectancy_engine_with_kelly_metrics():
    """Verify ExpectancyEngine outputs Kelly and RoR metrics correctly."""
    trades = [
        {"position_id": "1", "symbol": "BTC/USDT", "realized_pnl": 15.0},
        {"position_id": "2", "symbol": "ETH/USDT", "realized_pnl": -5.0},
        {"position_id": "3", "symbol": "SOL/USDT", "realized_pnl": 12.0},
        {"position_id": "4", "symbol": "NEAR/USDT", "realized_pnl": 8.75},
        {"position_id": "5", "symbol": "SUI/USDT", "realized_pnl": -4.0},
    ]
    res = ExpectancyEngine.calculate_from_positions(trades)
    assert res["sample_size"] == 5
    assert res["winning_trades"] == 3
    assert res["losing_trades"] == 2
    assert res["win_rate_pct"] == 60.0
    assert res["is_positive_expectancy"] is True
    assert res["profit_factor"] > 3.0
    assert "half_kelly_pct" in res
    assert "risk_of_ruin_pct" in res
    assert res["half_kelly_pct"] > 0.0
    assert res["risk_of_ruin_pct"] < 5.0
