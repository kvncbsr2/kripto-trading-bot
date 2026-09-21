import math
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP
from typing import Any, Dict, Optional, Tuple, Union

from shared.logging import get_logger

logger = get_logger("symbol-filters", service="execution")

# Default spot filter specs for common pairs (offline fallback / safety defaults)
# Includes top pairs, DeFi, Layer-1s, and popular micro-priced assets on Binance Spot
DEFAULT_FILTER_SPECS: Dict[str, Dict[str, float]] = {
    # High-cap majors
    "BTC/USDT": {"min_qty": 0.00001, "max_qty": 9000.0, "step_size": 0.00001, "tick_size": 0.01, "min_notional": 5.0},
    "ETH/USDT": {"min_qty": 0.0001, "max_qty": 9000.0, "step_size": 0.0001, "tick_size": 0.01, "min_notional": 5.0},
    "SOL/USDT": {"min_qty": 0.01, "max_qty": 90000.0, "step_size": 0.01, "tick_size": 0.01, "min_notional": 5.0},
    "BNB/USDT": {"min_qty": 0.001, "max_qty": 90000.0, "step_size": 0.001, "tick_size": 0.01, "min_notional": 5.0},
    "AVAX/USDT": {"min_qty": 0.01, "max_qty": 90000.0, "step_size": 0.01, "tick_size": 0.01, "min_notional": 5.0},
    "LINK/USDT": {"min_qty": 0.01, "max_qty": 90000.0, "step_size": 0.01, "tick_size": 0.001, "min_notional": 5.0},
    # Mid-range (~$0.1 - $5.0)
    "XRP/USDT": {"min_qty": 0.1, "max_qty": 9000000.0, "step_size": 0.1, "tick_size": 0.0001, "min_notional": 5.0},
    "ADA/USDT": {"min_qty": 0.1, "max_qty": 9000000.0, "step_size": 0.1, "tick_size": 0.0001, "min_notional": 5.0},
    "STX/USDT": {"min_qty": 0.1, "max_qty": 9000000.0, "step_size": 0.1, "tick_size": 0.0001, "min_notional": 5.0},
    "AXS/USDT": {"min_qty": 0.01, "max_qty": 900000.0, "step_size": 0.01, "tick_size": 0.001, "min_notional": 5.0},
    "GLM/USDT": {"min_qty": 0.1, "max_qty": 9000000.0, "step_size": 0.1, "tick_size": 0.0001, "min_notional": 5.0},
    "SUI/USDT": {"min_qty": 0.1, "max_qty": 9000000.0, "step_size": 0.1, "tick_size": 0.0001, "min_notional": 5.0},
    "MET/USDT": {"min_qty": 0.1, "max_qty": 9000000.0, "step_size": 0.1, "tick_size": 0.0001, "min_notional": 5.0},
    "FF/USDT": {"min_qty": 0.1, "max_qty": 9000000.0, "step_size": 0.1, "tick_size": 0.0001, "min_notional": 5.0},
    "JST/USDT": {"min_qty": 0.1, "max_qty": 9000000.0, "step_size": 0.1, "tick_size": 0.0001, "min_notional": 5.0},
    "DOGE/USDT": {"min_qty": 1.0, "max_qty": 9000000.0, "step_size": 1.0, "tick_size": 0.00001, "min_notional": 5.0},
    # Micro assets ($0.001 - $0.05)
    "BB/USDT": {"min_qty": 1.0, "max_qty": 90000000.0, "step_size": 1.0, "tick_size": 0.0001, "min_notional": 5.0},
    "ROBO/USDT": {"min_qty": 1.0, "max_qty": 90000000.0, "step_size": 1.0, "tick_size": 0.00001, "min_notional": 5.0},
    "IOST/USDT": {"min_qty": 1.0, "max_qty": 90000000.0, "step_size": 1.0, "tick_size": 0.00001, "min_notional": 5.0},
    "IQ/USDT": {"min_qty": 1.0, "max_qty": 90000000.0, "step_size": 1.0, "tick_size": 0.00001, "min_notional": 5.0},
    "VTHO/USDT": {"min_qty": 1.0, "max_qty": 90000000.0, "step_size": 1.0, "tick_size": 0.00001, "min_notional": 5.0},
    "DOGS/USDT": {"min_qty": 1.0, "max_qty": 90000000.0, "step_size": 1.0, "tick_size": 0.000001, "min_notional": 5.0},
    # Ultra-micro assets (< $0.001, meme coins)
    "PEPE/USDT": {"min_qty": 100.0, "max_qty": 9000000000.0, "step_size": 100.0, "tick_size": 0.00000001, "min_notional": 5.0},
    "SHIB/USDT": {"min_qty": 1.0, "max_qty": 9000000000.0, "step_size": 1.0, "tick_size": 0.00000001, "min_notional": 5.0},
    "BONK/USDT": {"min_qty": 100.0, "max_qty": 9000000000.0, "step_size": 100.0, "tick_size": 0.00000001, "min_notional": 5.0},
    "FLOKI/USDT": {"min_qty": 1.0, "max_qty": 9000000000.0, "step_size": 1.0, "tick_size": 0.00000001, "min_notional": 5.0},
    "1000SATS/USDT": {"min_qty": 10.0, "max_qty": 900000000.0, "step_size": 10.0, "tick_size": 0.0000001, "min_notional": 5.0},
}


@dataclass(frozen=True)
class SlippageCalculationResult:
    base_price: float
    raw_exec_price: float
    exec_price: float
    slippage_cost: float
    notional: float
    configured_slippage_bps: float
    effective_slippage_bps: float
    tick_rounding_impact_bps: float
    tick_tolerance_bps: float
    max_allowed_slippage_bps: float
    is_within_tolerance: bool
    side: str
    symbol: str


class SymbolFilterEngine:
    """
    Validates and normalizes order sizes and prices against Binance symbol filters:
    - LOT_SIZE: minQty, maxQty, stepSize
    - PRICE_FILTER: tickSize
    - MIN_NOTIONAL: minNotional ($5-$10 threshold)
    - Directional Adverse Slippage Normalization (BUY rounds up, SELL rounds down)
    - Decimal Precision Engine to eliminate floating point rounding errors
    """

    def __init__(self, filter_specs: Optional[Dict[str, Dict[str, float]]] = None):
        self.filters: Dict[str, Dict[str, float]] = dict(DEFAULT_FILTER_SPECS)
        if filter_specs:
            self.filters.update(filter_specs)

    def get_filter_spec(self, symbol: str, price: Optional[float] = None) -> Dict[str, float]:
        """Returns filter spec for symbol or generates standard Binance fallback by price magnitude."""
        spec = self.filters.get(symbol)
        if spec:
            return spec

        p = abs(price) if price is not None and price > 0 else 1.0
        if p >= 1000.0:
            tick_size = 0.01
            step_size = 0.00001
            max_qty = 9000.0
        elif p >= 100.0:
            tick_size = 0.01
            step_size = 0.0001
            max_qty = 90000.0
        elif p >= 1.0:
            tick_size = 0.0001
            step_size = 0.01
            max_qty = 900000.0
        elif p >= 0.1:
            tick_size = 0.0001
            step_size = 0.1
            max_qty = 9000000.0
        elif p >= 0.01:
            tick_size = 0.00001
            step_size = 1.0
            max_qty = 90000000.0
        elif p >= 0.001:
            tick_size = 0.000001
            step_size = 1.0
            max_qty = 900000000.0
        elif p >= 0.0001:
            tick_size = 0.0000001
            step_size = 10.0
            max_qty = 9000000000.0
        else:
            tick_size = 0.00000001
            step_size = 100.0
            max_qty = 90000000000.0

        fallback_spec = {
            "min_qty": step_size,
            "max_qty": max_qty,
            "step_size": step_size,
            "tick_size": tick_size,
            "min_notional": 5.0,
        }
        return fallback_spec

    def load_from_exchange_info(self, exchange_markets: Dict[str, Any]):
        """Populates filters dynamically from ccxt load_markets / fetch_markets or raw Binance exchangeInfo."""
        for symbol, market in exchange_markets.items():
            precision = market.get("precision", {})
            limits = market.get("limits", {})
            amount_limits = limits.get("amount", {})
            cost_limits = limits.get("cost", {})

            min_qty = float(amount_limits.get("min") or 0.0001)
            max_qty = float(amount_limits.get("max") or 1000000.0)

            raw_step = precision.get("amount")
            if raw_step is not None:
                step_size = float(raw_step)
                if isinstance(raw_step, int) and raw_step > 0 and step_size >= 1:
                    step_size = 10 ** (-int(raw_step))
            else:
                step_size = 0.0001

            raw_tick = precision.get("price")
            if raw_tick is not None:
                tick_size = float(raw_tick)
                if isinstance(raw_tick, int) and raw_tick > 0 and tick_size >= 1:
                    tick_size = 10 ** (-int(raw_tick))
            else:
                tick_size = 0.01

            min_notional = float(cost_limits.get("min") or 5.0)

            # Parse Binance native filters directly if available in market['info']['filters']
            info = market.get("info", {})
            raw_filters = info.get("filters", []) if isinstance(info, dict) else []
            for f in raw_filters:
                ft = f.get("filterType")
                if ft == "PRICE_FILTER":
                    if "tickSize" in f and float(f["tickSize"]) > 0:
                        tick_size = float(f["tickSize"])
                elif ft == "LOT_SIZE":
                    if "stepSize" in f and float(f["stepSize"]) > 0:
                        step_size = float(f["stepSize"])
                    if "minQty" in f and float(f["minQty"]) > 0:
                        min_qty = float(f["minQty"])
                    if "maxQty" in f and float(f["maxQty"]) > 0:
                        max_qty = float(f["maxQty"])
                elif ft in ("MIN_NOTIONAL", "NOTIONAL"):
                    if "minNotional" in f and float(f["minNotional"]) > 0:
                        min_notional = float(f["minNotional"])

            self.filters[symbol] = {
                "min_qty": min_qty,
                "max_qty": max_qty,
                "step_size": step_size,
                "tick_size": tick_size,
                "min_notional": min_notional,
            }

    @staticmethod
    def _to_decimal(value: Union[float, int, str, Decimal]) -> Decimal:
        if isinstance(value, Decimal):
            return value
        # Use str representation with high precision to avoid binary float artifacts
        return Decimal(f"{value:.12f}".rstrip("0").rstrip(".")) if isinstance(value, float) else Decimal(str(value))

    @classmethod
    def round_to_step_size(cls, value: float, step_size: Union[float, str]) -> float:
        """Truncates quantity down to exact stepSize multiple using Decimal arithmetic."""
        if isinstance(step_size, str):
            step_size = cls().get_filter_spec(step_size)["step_size"]
        if step_size <= 0 or value <= 0:
            return 0.0
        d_val = cls._to_decimal(value)
        d_step = cls._to_decimal(step_size)
        steps = (d_val / d_step).quantize(Decimal("1"), rounding=ROUND_FLOOR)
        res = steps * d_step
        return float(res)

    @classmethod
    def round_to_tick_size(
        cls, value: float, tick_size: Union[float, str], direction: str = "NEUTRAL"
    ) -> float:
        """
        Normalizes price to tickSize.
        - direction="BUY": rounds UP (ROUND_CEILING) to model adverse slippage fill
        - direction="SELL": rounds DOWN (ROUND_FLOOR) to model adverse slippage fill
        - direction="NEUTRAL": standard nearest tick rounding (ROUND_HALF_UP)
        """
        if isinstance(tick_size, str):
            tick_size = cls().get_filter_spec(tick_size, price=value)["tick_size"]
        if tick_size <= 0 or value <= 0:
            return value
        d_val = cls._to_decimal(value)
        d_tick = cls._to_decimal(tick_size)

        if str(direction).upper() in {"BUY", "UP", "CEIL"}:
            steps = (d_val / d_tick).quantize(Decimal("1"), rounding=ROUND_CEILING)
        elif str(direction).upper() in {"SELL", "DOWN", "FLOOR"}:
            steps = (d_val / d_tick).quantize(Decimal("1"), rounding=ROUND_FLOOR)
        else:
            steps = (d_val / d_tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP)

        res = steps * d_tick
        return float(res)

    def normalize_and_validate(
        self, symbol: str, price: float, quantity: float, side: str = "NEUTRAL"
    ) -> Tuple[bool, Optional[str], float, float]:
        """
        Normalizes quantity to stepSize (floor) and price to tickSize (directional).
        Validates minQty, maxQty, and minNotional.
        Returns (is_valid, reason, normalized_price, normalized_quantity).
        """
        spec = self.get_filter_spec(symbol, price=price)
        norm_qty = self.round_to_step_size(quantity, spec["step_size"])
        norm_price = self.round_to_tick_size(price, spec["tick_size"], direction=side)

        if quantity <= 0:
            return False, f"Quantity {quantity} must be positive and >= minQty {spec['min_qty']}", norm_price, 0.0

        if norm_qty <= 0:
            return False, f"Quantity {quantity} rounded to zero under stepSize {spec['step_size']} (below minQty {spec['min_qty']})", norm_price, 0.0

        if norm_qty > spec["max_qty"]:
            return False, f"Quantity {norm_qty} exceeds maxQty {spec['max_qty']}", norm_price, norm_qty

        if norm_qty < spec["min_qty"]:
            return False, f"Quantity {norm_qty} below minQty {spec['min_qty']}", norm_price, norm_qty

        notional = norm_price * norm_qty
        if notional < spec["min_notional"]:
            return False, f"Notional value ${notional:.2f} below minNotional ${spec['min_notional']:.2f}", norm_price, norm_qty

        return True, None, norm_price, norm_qty

    def calculate_execution_slippage(
        self,
        base_price: float,
        quantity: float,
        side: str,
        slippage_rate: float = 0.0005,  # 5 bps default
        symbol: str = "BTC/USDT",
        execution_style: str = "TAKER",
    ) -> SlippageCalculationResult:
        """
        Calculates adverse execution price, slippage cost, and validates that effective slippage
        does not exceed configured slippage beyond the explainable granularity of 1 tickSize.
        """
        spec = self.get_filter_spec(symbol, price=base_price)
        tick_size = spec["tick_size"]
        side_upper = str(side).upper()

        d_base = self._to_decimal(base_price)
        d_qty = self._to_decimal(quantity)
        d_slip_rate = self._to_decimal(slippage_rate)
        d_tick = self._to_decimal(tick_size)

        configured_bps = float(d_slip_rate * Decimal("10000.0"))

        if execution_style.upper() == "TAKER" and slippage_rate > 0:
            if side_upper == "BUY":
                d_raw_exec = d_base * (Decimal("1.0") + d_slip_rate)
                # BUY adverse fill rounds UP to the tick boundary
                d_exec = (d_raw_exec / d_tick).quantize(Decimal("1"), rounding=ROUND_CEILING) * d_tick
            else:
                d_raw_exec = d_base * (Decimal("1.0") - d_slip_rate)
                # SELL adverse fill rounds DOWN to the tick boundary
                d_exec = (d_raw_exec / d_tick).quantize(Decimal("1"), rounding=ROUND_FLOOR) * d_tick
        else:
            # MAKER / Zero slippage
            d_raw_exec = d_base
            d_exec = (d_raw_exec / d_tick).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * d_tick

        raw_exec_price = float(d_raw_exec)
        exec_price = float(d_exec)

        d_diff = abs(d_exec - d_base)
        d_slip_cost = d_diff * d_qty
        d_notional = d_exec * d_qty

        slippage_cost = float(d_slip_cost)
        notional = float(d_notional)

        # Calculate effective slippage in basis points
        if d_base > 0:
            effective_bps = float((d_diff / d_base) * Decimal("10000.0"))
            tick_tol_bps = float((d_tick / d_base) * Decimal("10000.0"))
        else:
            effective_bps = 0.0
            tick_tol_bps = 0.0

        tick_rounding_impact_bps = effective_bps - configured_bps

        # Explainable tolerance: configured bps + up to 1 tick size granularity (+ 0.5 bps arithmetic buffer)
        max_allowed_bps = configured_bps + tick_tol_bps + 0.5
        is_within_tolerance = effective_bps <= max_allowed_bps

        return SlippageCalculationResult(
            base_price=base_price,
            raw_exec_price=raw_exec_price,
            exec_price=exec_price,
            slippage_cost=slippage_cost,
            notional=notional,
            configured_slippage_bps=round(configured_bps, 2),
            effective_slippage_bps=round(effective_bps, 2),
            tick_rounding_impact_bps=round(tick_rounding_impact_bps, 2),
            tick_tolerance_bps=round(tick_tol_bps, 2),
            max_allowed_slippage_bps=round(max_allowed_bps, 2),
            is_within_tolerance=is_within_tolerance,
            side=side_upper,
            symbol=symbol,
        )


# Authoritative singleton instance
symbol_filter_engine = SymbolFilterEngine()
