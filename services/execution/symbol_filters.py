import math
from typing import Any, Dict, Optional, Tuple

from shared.logging import get_logger

logger = get_logger("symbol-filters", service="execution")

# Default spot filter specs for common pairs (offline fallback / safety defaults)
DEFAULT_FILTER_SPECS: Dict[str, Dict[str, float]] = {
    "BTC/USDT": {"min_qty": 0.00001, "max_qty": 9000.0, "step_size": 0.00001, "tick_size": 0.01, "min_notional": 5.0},
    "ETH/USDT": {"min_qty": 0.0001, "max_qty": 9000.0, "step_size": 0.0001, "tick_size": 0.01, "min_notional": 5.0},
    "SOL/USDT": {"min_qty": 0.01, "max_qty": 90000.0, "step_size": 0.01, "tick_size": 0.01, "min_notional": 5.0},
    "BNB/USDT": {"min_qty": 0.001, "max_qty": 90000.0, "step_size": 0.001, "tick_size": 0.01, "min_notional": 5.0},
    "XRP/USDT": {"min_qty": 0.1, "max_qty": 9000000.0, "step_size": 0.1, "tick_size": 0.0001, "min_notional": 5.0},
    "DOGE/USDT": {"min_qty": 1.0, "max_qty": 9000000.0, "step_size": 1.0, "tick_size": 0.00001, "min_notional": 5.0},
    "ADA/USDT": {"min_qty": 0.1, "max_qty": 9000000.0, "step_size": 0.1, "tick_size": 0.0001, "min_notional": 5.0},
    "AVAX/USDT": {"min_qty": 0.01, "max_qty": 90000.0, "step_size": 0.01, "tick_size": 0.01, "min_notional": 5.0},
    "LINK/USDT": {"min_qty": 0.01, "max_qty": 90000.0, "step_size": 0.01, "tick_size": 0.001, "min_notional": 5.0},
}


class SymbolFilterEngine:
    """
    Validates and normalizes order sizes and prices against Binance symbol filters:
    - LOT_SIZE: minQty, maxQty, stepSize
    - PRICE_FILTER: tickSize
    - MIN_NOTIONAL: minNotional ($5-$10 threshold)
    """

    def __init__(self, filter_specs: Optional[Dict[str, Dict[str, float]]] = None):
        self.filters: Dict[str, Dict[str, float]] = filter_specs or dict(DEFAULT_FILTER_SPECS)

    def load_from_exchange_info(self, exchange_markets: Dict[str, Any]):
        """Populates filters dynamically from ccxt load_markets / fetch_markets."""
        for symbol, market in exchange_markets.items():
            precision = market.get("precision", {})
            limits = market.get("limits", {})
            amount_limits = limits.get("amount", {})
            cost_limits = limits.get("cost", {})

            min_qty = float(amount_limits.get("min") or 0.0001)
            max_qty = float(amount_limits.get("max") or 1000000.0)
            step_size = float(precision.get("amount") or 0.0001)
            if step_size < 1:
                step_size = 10 ** (-int(step_size)) if isinstance(step_size, int) else step_size
            tick_size = float(precision.get("price") or 0.01)
            min_notional = float(cost_limits.get("min") or 5.0)

            self.filters[symbol] = {
                "min_qty": min_qty,
                "max_qty": max_qty,
                "step_size": step_size,
                "tick_size": tick_size,
                "min_notional": min_notional,
            }

    @staticmethod
    def round_to_step_size(value: float, step_size: float) -> float:
        if step_size <= 0:
            return value
        precision = max(0, int(round(-math.log10(step_size)))) if step_size < 1 else 0
        factor = 1.0 / step_size
        rounded = math.floor(round(value * factor, 8)) / factor
        return round(rounded, precision)

    @staticmethod
    def round_to_tick_size(value: float, tick_size: float) -> float:
        if tick_size <= 0:
            return value
        precision = max(0, int(round(-math.log10(tick_size)))) if tick_size < 1 else 0
        factor = 1.0 / tick_size
        rounded = round(round(value * factor, 8)) / factor
        return round(rounded, precision)

    def normalize_and_validate(
        self, symbol: str, price: float, quantity: float
    ) -> Tuple[bool, Optional[str], float, float]:
        """
        Normalizes quantity to stepSize and price to tickSize.
        Validates minQty, maxQty, and minNotional.
        Returns (is_valid, reason, normalized_price, normalized_quantity).
        """
        spec = self.filters.get(symbol)
        if not spec:
            # Dynamic fallback filter based on price magnitude
            if price >= 100.0:
                tick_size = 0.01
                step_size = 0.0001
                max_qty = 1000000.0
            elif price >= 1.0:
                tick_size = 0.001
                step_size = 0.01
                max_qty = 10000000.0
            elif price >= 0.01:
                tick_size = 0.0001
                step_size = 0.1
                max_qty = 100000000.0
            else:
                tick_size = 0.000001
                step_size = 1.0
                max_qty = 1000000000.0

            spec = {
                "min_qty": step_size,
                "max_qty": max_qty,
                "step_size": step_size,
                "tick_size": tick_size,
                "min_notional": 5.0,
            }

        norm_qty = self.round_to_step_size(quantity, spec["step_size"])
        norm_price = self.round_to_tick_size(price, spec["tick_size"])

        # FIX (2026-09): previously silently clamped norm_qty down to max_qty and
        # kept going. Directionally that's risk-reducing (smaller size), but it means
        # the order actually sent no longer matches what RiskEngine calculated for
        # position sizing — a silent deviation with no record of it happening. Reject
        # instead, exactly like the minQty case below, so the caller (and logs) see
        # a clear reason rather than a silently different order.
        if norm_qty > spec["max_qty"]:
            return False, f"Quantity {norm_qty} exceeds maxQty {spec['max_qty']}", norm_price, norm_qty

        if norm_qty < spec["min_qty"]:
            return False, f"Quantity {norm_qty} below minQty {spec['min_qty']}", norm_price, norm_qty

        notional = norm_price * norm_qty
        if notional < spec["min_notional"]:
            return False, f"Notional value ${notional:.2f} below minNotional ${spec['min_notional']:.2f}", norm_price, norm_qty

        return True, None, norm_price, norm_qty


# Authoritative singleton instance
symbol_filter_engine = SymbolFilterEngine()
