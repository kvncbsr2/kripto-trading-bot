import pytest

from services.execution.symbol_filters import SymbolFilterEngine


def test_symbol_filter_normalization_and_validation():
    """Verifies LOT_SIZE step size rounding and minNotional validation (AUDIT-11)."""
    filters = {
        "BTC/USDT": {
            "min_qty": 0.00001,
            "max_qty": 1000.0,
            "step_size": 0.00001,
            "tick_size": 0.01,
            "min_notional": 5.0,
        }
    }
    engine = SymbolFilterEngine(filter_specs=filters)

    # 1. Step size rounding
    raw_qty = 0.00123456789
    price = 60000.12345
    is_valid, reason, norm_price, norm_qty = engine.normalize_and_validate("BTC/USDT", price, raw_qty)

    assert is_valid is True
    assert norm_qty == 0.00123  # Rounded down to 5 decimals
    assert norm_price == 60000.12  # Rounded to 2 decimals

    # 2. Min notional rejection
    tiny_qty = 0.00001  # 0.00001 * 100 = $0.001 < $5.0
    cheap_price = 100.0
    is_valid_tiny, reason_tiny, _, _ = engine.normalize_and_validate("BTC/USDT", cheap_price, tiny_qty)

    assert is_valid_tiny is False
    assert "below minNotional" in reason_tiny
