import gc
import os
import sqlite3
import tempfile
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pytest

from services.config_manager.risk_profiles import (
    RISK_PROFILES,
    get_profile,
    apply_profile_to_system,
)
from services.config_manager.state_persistence import (
    load_persisted_profile_state,
    save_persisted_profile_state,
)
from services.risk_engine.risk_engine import RiskEngine
from services.risk_engine.circuit_breaker import CircuitBreaker
from services.execution.paper_execution import (
    PaperExecutionEngine,
    PositionSide,
    PositionStatus,
    get_price_precision,
)
from shared.schemas import RiskDecision
from shared.enums import SignalDirection


class TestRiskProfileAndStartupInvariants:
    """Remediation tests for Component 1: Risk engine, L1 startup, and safety invariants."""

    def test_l1_profile_specs(self):
        """Invariant: Level 1 must have max risk 0.5%, max 2 positions, target $50, max loss $50."""
        l1 = RISK_PROFILES[1]
        assert l1.risk_per_trade == 0.005
        assert l1.max_open_positions == 2
        assert l1.daily_target == 50.0
        assert l1.daily_max_loss == 50.0

    def test_l10_hard_invariants(self):
        """Invariant: Even Level 10 cannot exceed 8 open positions or $200 max loss."""
        l10 = RISK_PROFILES[10]
        assert l10.max_open_positions <= 8
        assert l10.daily_max_loss <= 200.0

    def test_hard_position_ceiling_clamping(self):
        """Invariant: Max open positions override cannot exceed 8, regardless of input."""
        re = RiskEngine(max_open_positions=2)
        # Attempt to set 25
        re.set_max_open_positions_override(25)
        assert re.effective_max_open_positions == 8
        assert re.max_open_positions_override == 8

        # Test another large number
        re.set_max_open_positions_override(50)
        assert re.effective_max_open_positions == 8
        assert re.max_open_positions_override == 8

    def test_state_persistence_clamps_override(self, tmp_path):
        """Invariant: Loading a state file with override=25 clamps it to <= 8."""
        test_file = tmp_path / "runtime_state.json"
        save_persisted_profile_state(
            risk_level=3,
            max_open_positions_override=25,
            file_path=test_file,
        )
        loaded = load_persisted_profile_state(file_path=test_file)
        assert loaded["max_open_positions_override"] == 8

    def test_circuit_breaker_active_by_default(self):
        """Invariant: Circuit breaker must boot active (not suspended)."""
        cb = CircuitBreaker()
        assert not cb.is_suspended

    def test_zero_position_eviction_on_downgrade(self):
        """Invariant: Downgrading risk level must NEVER liquidate or evict open positions."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        broker = None
        try:
            broker = PaperExecutionEngine(db_path=db_path)
            # Create a mock position
            decision = RiskDecision(
                approved=True,
                symbol="BTC/USDT",
                direction=SignalDirection.LONG,
                entry_price=60000.0,
                calculated_size=0.1,
                reason="test",
            )
            broker.execute_market_order(decision, strategy_name="test_strat")
            assert len(broker.open_positions) == 1

            # Downgrade system profile to Level 1
            apply_profile_to_system(1, source="test_downgrade")

            # Broker positions must still be intact
            assert len(broker.open_positions) == 1
            assert "BTC/USDT" in broker.open_positions
            assert broker.open_positions["BTC/USDT"].status == PositionStatus.OPEN
        finally:
            broker = None
            gc.collect()
            if os.path.exists(db_path):
                try:
                    os.unlink(db_path)
                except Exception:
                    pass


class TestAccountingAndPnLInvariants:
    """Remediation tests for Component 2: Daily vs Lifetime PnL and partial TP accounting."""

    def test_price_precision_helper(self):
        """Verify dynamic precision helper for micro and macro asset prices."""
        assert get_price_precision(65000.0) == 2
        assert get_price_precision(150.50) == 2
        assert get_price_precision(9.99) == 4
        assert get_price_precision(0.05) == 6
        assert get_price_precision(0.00854) == 8
        assert get_price_precision(0.0005) == 8
        assert get_price_precision(0.00005) == 10

    def test_micro_price_slippage_not_zeroed(self):
        """Invariant: Slippage on micro assets (e.g. $0.00854) must retain price impact."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        broker = None
        try:
            broker = PaperExecutionEngine(db_path=db_path, slippage_bps=5.0)  # 5 bps
            base_price = 0.008542
            decision = RiskDecision(
                approved=True,
                symbol="MICRO/USDT",
                direction=SignalDirection.LONG,
                entry_price=base_price,
                calculated_size=10000.0,
                reason="micro test",
            )
            order, fill, pos = broker.execute_market_order(decision, execution_style="TAKER")
            # exec_price must NOT be equal to base_price (slippage applied)
            assert fill.price > base_price
            # Slippage cost must be > 0
            assert fill.slippage > 0
            assert abs(fill.price - base_price) > 0
        finally:
            broker = None
            gc.collect()
            if os.path.exists(db_path):
                try:
                    os.unlink(db_path)
                except Exception:
                    pass

    def test_daily_vs_lifetime_pnl_separation(self):
        """Invariant: Daily PnL tracks today's performance, distinct from lifetime realized PnL."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        broker = None
        try:
            broker = PaperExecutionEngine(db_path=db_path, initial_balance=10000.0)

            # Insert an old closed trade from yesterday directly into DB
            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            yesterday_str = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
            c.execute("""
                INSERT INTO paper_positions (
                    position_id, symbol, side, entry_price, current_price, quantity,
                    stop_loss, take_profit, unrealized_pnl, realized_pnl, fees_paid, status, strategy,
                    opened_at, closed_at
                ) VALUES (
                    'pos_yesterday', 'ETH/USDT', 'LONG', 3000.0, 3100.0, 1.0,
                    2900.0, 3200.0, 0.0, 95.0, 5.0, 'CLOSED', 'strat_1',
                    ?, ?
                )
            """, (yesterday_str, yesterday_str))
            # Update account balance to reflect yesterday's closed trade
            c.execute("UPDATE paper_account SET balance = 10095.0, available_balance = 10095.0 WHERE id = 1")
            conn.commit()
            conn.close()

            # Restore state in broker
            broker._restore_state_from_db()

            # Verify lifetime vs daily PnL
            assert broker.lifetime_realized_net_pnl == 95.0
            assert broker.daily_realized_net_pnl == 0.0  # Zero closed today!
            assert broker.daily_total_pnl == 0.0

            # Now open a position today and calculate snapshot
            decision = RiskDecision(
                approved=True,
                symbol="SOL/USDT",
                direction=SignalDirection.LONG,
                entry_price=100.0,
                calculated_size=10.0,
                reason="sol test",
            )
            broker.execute_market_order(decision)
            # Update market price to $105 (+ $50 upnl gross, roughly)
            broker.update_market_price("SOL/USDT", 105.0)

            snapshot = broker.get_portfolio_snapshot()
            assert snapshot["lifetime_realized_net_pnl"] == 95.0
            assert snapshot["daily_realized_net_pnl"] == 0.0
            # daily_total_pnl must only be the unrealized PnL of today's position, NOT including yesterday's $95!
            assert snapshot["daily_total_pnl"] == snapshot["unrealized_pnl"]
            assert snapshot["daily_total_pnl"] != snapshot["lifetime_realized_net_pnl"] + snapshot["unrealized_pnl"]
        finally:
            broker = None
            gc.collect()
            if os.path.exists(db_path):
                try:
                    os.unlink(db_path)
                except Exception:
                    pass

    def test_partial_tp_prior_day_no_double_counting(self):
        """Invariant: Partial TP executed yesterday must not be double-counted when closed today."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        broker = None
        try:
            broker = PaperExecutionEngine(db_path=db_path, initial_balance=10000.0)

            # Insert an open position that had a partial TP executed yesterday
            yesterday_str = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            c.execute("""
                INSERT INTO paper_positions (
                    position_id, symbol, side, entry_price, current_price, quantity,
                    stop_loss, take_profit, unrealized_pnl, realized_pnl, fees_paid, status, strategy,
                    opened_at, closed_at, partial_tp_hit, partial_realized_pnl,
                    partial_fees_paid, partial_realized_at
                ) VALUES (
                    'pos_partial', 'BTC/USDT', 'LONG', 50000.0, 51000.0, 0.5,
                    50000.0, 55000.0, 500.0, 0.0, 2.5, 'OPEN', 'strat_tp',
                    ?, NULL, 1, 50.0, 2.5, ?
                )
            """, (yesterday_str, yesterday_str))
            c.execute("UPDATE paper_account SET balance = 10050.0, available_balance = 7550.0, reserved_balance = 25000.0 WHERE id = 1")
            conn.commit()
            conn.close()

            broker._restore_state_from_db()
            assert "BTC/USDT" in broker.open_positions

            # Today: final close at $52,000.
            closed_pos = broker.close_position("BTC/USDT", exit_price=52000.0, reason="FINAL_TP")
            assert closed_pos is not None

            # Lifetime realized net PnL includes yesterday's $50 + today's leg net PnL
            assert broker.lifetime_realized_net_pnl > 1000.0
            # Daily realized net PnL must ONLY include today's leg net PnL (~$971), NOT the $50 from yesterday!
            assert broker.daily_realized_net_pnl < broker.lifetime_realized_net_pnl
            assert round(broker.daily_realized_net_pnl + 50.0, 1) == round(broker.lifetime_realized_net_pnl, 1)
        finally:
            broker = None
            gc.collect()
            if os.path.exists(db_path):
                try:
                    os.unlink(db_path)
                except Exception:
                    pass


class TestPriceFreshnessPersistence:
    """Remediation tests for Component 4: SQLite persistence of price freshness."""

    def test_sqlite_persists_price_freshness(self):
        """Invariant: broker.update_market_price persists price_stale, failures, and timestamp to SQLite."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        broker = None
        try:
            broker = PaperExecutionEngine(db_path=db_path)
            decision = RiskDecision(
                approved=True,
                symbol="AVAX/USDT",
                direction=SignalDirection.LONG,
                entry_price=25.0,
                calculated_size=10.0,
                reason="freshness test",
            )
            broker.execute_market_order(decision)

            # Update market price
            broker.update_market_price("AVAX/USDT", 26.50)

            # Directly inspect SQLite database
            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            c.execute("""
                SELECT current_price, price_stale, price_fetch_failures, last_price_update_at
                FROM paper_positions WHERE symbol = 'AVAX/USDT'
            """)
            row = c.fetchone()
            conn.close()

            assert row is not None
            assert row[0] == 26.50
            assert row[1] == 0  # not stale
            assert row[2] == 0  # 0 failures
            assert row[3] is not None  # timestamp exists

            # Now simulate 5 price fetch failures on position
            pos = broker.open_positions["AVAX/USDT"]
            pos.price_fetch_failures = 5
            pos.price_stale = True
            broker._persist_position(pos)

            # Verify persisted state
            conn = sqlite3.connect(db_path)
            c = conn.cursor()
            c.execute("""
                SELECT price_stale, price_fetch_failures
                FROM paper_positions WHERE symbol = 'AVAX/USDT'
            """)
            row2 = c.fetchone()
            conn.close()

            assert row2[0] == 1  # stale == True
            assert row2[1] == 5  # failures == 5
        finally:
            broker = None
            gc.collect()
            if os.path.exists(db_path):
                try:
                    os.unlink(db_path)
                except Exception:
                    pass
