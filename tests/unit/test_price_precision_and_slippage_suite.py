import os
import tempfile
import pytest
from decimal import Decimal
from services.execution.symbol_filters import (
    SymbolFilterEngine,
    symbol_filter_engine,
    SlippageCalculationResult,
)
from services.execution.paper_execution import PaperExecutionEngine
from shared.enums import OrderSide, PositionSide, SignalDirection
from shared.schemas import RiskDecision, Position


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    if os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass


@pytest.fixture
def engine(temp_db):
    eng = PaperExecutionEngine(initial_balance=10000.0, db_path=temp_db, slippage_rate=0.0005, taker_fee=0.001)
    return eng


class TestPricePrecisionAndSlippageSuite:
    """
    Test suite verifying precision, slippage calculation, Binance symbol filters,
    and tolerance breaches across all asset price tiers.
    """

    def test_scenario_1_high_price_asset_btc(self):
        """1. High-price asset (BTC/USDT @ ~$60,000): tickSize = 0.01, 5 bps slippage."""
        base_price = 60000.0
        qty = 0.5
        res = symbol_filter_engine.calculate_execution_slippage(
            base_price=base_price,
            quantity=qty,
            side="BUY",
            slippage_rate=0.0005,  # 5 bps
            symbol="BTC/USDT",
        )
        # 60000 * 1.0005 = 60030.00. Exactly on a 0.01 tick.
        assert res.exec_price == 60030.00
        assert res.configured_slippage_bps == 5.00
        assert res.effective_slippage_bps == 5.00
        assert res.is_within_tolerance is True
        assert res.tick_rounding_impact_bps == 0.00

    def test_scenario_2_one_usdt_asset(self):
        """2. ~1 USDT asset (ADA/USDT or STX/USDT @ ~$1.00): tickSize = 0.0001, 5 bps slippage."""
        base_price = 1.0000
        qty = 500.0
        res = symbol_filter_engine.calculate_execution_slippage(
            base_price=base_price,
            quantity=qty,
            side="BUY",
            slippage_rate=0.0005,
            symbol="ADA/USDT",
        )
        # 1.0000 * 1.0005 = 1.0005. Exactly on 0.0001 tick boundary.
        assert res.exec_price == 1.0005
        assert res.configured_slippage_bps == 5.00
        assert res.effective_slippage_bps == 5.00
        assert res.is_within_tolerance is True

    def test_scenario_3_sub_cent_micro_asset_dogs(self):
        """
        3. <0.01 USDT asset (DOGS/USDT @ $0.0085):
        Previously, round(price, 4) would round $0.0085 to 0.0085, completely destroying 5 bps slippage
        or jumping by 100+ bps if rounded. With 0.000001 tickSize, slippage is ~5.88 bps (within 1 tick tolerance).
        """
        base_price = 0.0085
        qty = 10000.0
        res_buy = symbol_filter_engine.calculate_execution_slippage(
            base_price=base_price,
            quantity=qty,
            side="BUY",
            slippage_rate=0.0005,
            symbol="DIG/USDT",
        )
        # raw: 0.0085 * 1.0005 = 0.00850425 -> ceil to 0.000001 = 0.008505
        assert res_buy.exec_price == 0.008505
        # effective bps: (0.000005 / 0.0085) * 10000 = ~5.88 bps
        assert 5.0 <= res_buy.effective_slippage_bps <= 6.5
        assert res_buy.is_within_tolerance is True
        # Verify it did NOT round to 4 decimals (which would be 0.0085 = 0 bps)
        assert res_buy.exec_price != 0.0085

    def test_scenario_4_sub_milli_micro_asset_pepe(self):
        """4. <0.001 USDT asset (PEPE/USDT @ $0.000015): tickSize = 0.00000001."""
        base_price = 0.000015
        qty = 10_000_000.0
        res = symbol_filter_engine.calculate_execution_slippage(
            base_price=base_price,
            quantity=qty,
            side="BUY",
            slippage_rate=0.0005,
            symbol="PEPE/USDT",
        )
        # raw: 0.000015 * 1.0005 = 0.0000150075 -> ceil to 0.00000001 = 0.00001501
        assert res.exec_price == 0.00001501
        # effective bps: (0.00000001 / 0.000015) * 10000 = ~6.67 bps
        assert 5.0 <= res.effective_slippage_bps <= 7.0
        assert res.is_within_tolerance is True

    def test_scenario_5_large_quantity_micro_coin_step_size(self):
        """5. Large quantity micro coin (1,234,567.89 DOGS): quantity truncated to stepSize."""
        symbol = "DOGS/USDT"
        raw_qty = 1234567.89123
        norm_qty = symbol_filter_engine.round_to_step_size(raw_qty, symbol)
        # DOGS step_size is 1.0
        assert norm_qty == 1234567.0
        assert norm_qty <= raw_qty

    def test_scenario_6_directional_adverse_rounding_buy_vs_sell(self):
        """
        6. Directional rounding:
            - BUY rounds UP (ROUND_CEILING): buyer pays at least as much as slippage price.
            - SELL rounds DOWN (ROUND_FLOOR): seller receives at most slippage price.
        """
        base_price = 10.333333
        qty = 10.0
        res_buy = symbol_filter_engine.calculate_execution_slippage(
            base_price=base_price,
            quantity=qty,
            side="BUY",
            slippage_rate=0.0005,
            symbol="UNKNOWN/USDT",
        )
        res_sell = symbol_filter_engine.calculate_execution_slippage(
            base_price=base_price,
            quantity=qty,
            side="SELL",
            slippage_rate=0.0005,
            symbol="UNKNOWN/USDT",
        )
        assert res_buy.exec_price > base_price
        assert res_buy.exec_price >= res_buy.raw_exec_price
        assert res_sell.exec_price < base_price
        assert res_sell.exec_price <= res_sell.raw_exec_price

    def test_scenario_7_partial_exit_slippage_and_fill(self, engine):
        """7. Partial exit correctly applies directional slippage and populates fill metrics."""
        dec = RiskDecision(
            approved=True,
            symbol="BTC/USDT",
            direction=SignalDirection.LONG,
            entry_price=60000.0,
            stop_loss=58000.0,
            take_profit=64000.0,
            calculated_size=0.1,
        )
        engine.execute_market_order(dec, execution_style="TAKER")

        pos = engine.execute_partial_exit("BTC/USDT", fraction=0.5, exit_price=62000.0)
        assert pos is not None
        assert pos.quantity == 0.05
        assert pos.partial_tp_hit is True

        exit_fill = engine.fills[-1]
        assert exit_fill.side == OrderSide.SELL
        assert exit_fill.configured_slippage_bps == 5.0
        assert exit_fill.effective_slippage_bps is not None
        assert exit_fill.legacy_precision_affected is False
        assert exit_fill.price == 61969.00

    def test_scenario_8_close_position_gap_stop(self, engine):
        """8. Gap stop / position close applies directional slippage and populates fill metrics."""
        dec = RiskDecision(
            approved=True,
            symbol="SOL/USDT",
            direction=SignalDirection.LONG,
            entry_price=100.0,
            stop_loss=95.0,
            take_profit=110.0,
            calculated_size=5.0,
        )
        engine.execute_market_order(dec, execution_style="TAKER")

        closed_pos = engine.close_position("SOL/USDT", exit_price=94.0, reason="STOP_LOSS")
        assert closed_pos is not None
        assert closed_pos.status.value == "CLOSED"

        exit_fill = engine.fills[-1]
        assert exit_fill.side == OrderSide.SELL
        assert exit_fill.configured_slippage_bps == 5.0
        assert exit_fill.price == 93.95
        assert exit_fill.legacy_precision_affected is False

    def test_scenario_9_tolerance_breach_rejection(self, engine):
        """
        9. Tolerance breach: If slippage rate exceeds allowed tolerance, order is rejected
        and risk event is logged.
        """
        dec = RiskDecision(
            approved=True,
            symbol="BTC/USDT",
            direction=SignalDirection.LONG,
            entry_price=60000.0,
            stop_loss=58000.0,
            take_profit=64000.0,
            calculated_size=0.1,
        )
        from unittest.mock import patch
        with patch.object(
            symbol_filter_engine,
            "calculate_execution_slippage",
            return_value=SlippageCalculationResult(
                base_price=60000.0,
                raw_exec_price=66000.0,
                exec_price=66000.0,
                slippage_cost=600.0,
                notional=6600.0,
                configured_slippage_bps=5.0,
                effective_slippage_bps=1000.0,
                tick_rounding_impact_bps=995.0,
                tick_tolerance_bps=0.02,
                max_allowed_slippage_bps=5.52,
                is_within_tolerance=False,
                side="BUY",
                symbol="BTC/USDT",
            )
        ):
            with pytest.raises(ValueError, match="Excessive slippage rejected"):
                engine.execute_market_order(dec, execution_style="TAKER")
