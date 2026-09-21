"""
Unit Tests for Advanced Representation & Preference Learning:
- RevIN (Reversible Instance Normalization)
- DPO (Direct Preference Optimization) Preference Engine & Data Flywheel
- DPO Signal Gate Evaluator
- Institutional Evaluation Harness
"""

from datetime import datetime, timezone
import numpy as np
import pandas as pd
import pytest

from services.learning.revin_normalizer import RevINNormalizer
from services.learning.preference_engine import PreferenceEngine
from services.learning.dpo_signal_gate import DPOSignalGate
from services.learning.evaluation_harness import EvaluationHarness
from shared.schemas import Signal, SignalDirection


# =====================================================================
# 1. REVIN NORMALIZER TESTS
# =====================================================================

def test_revin_numerical_reversibility():
    """RevIN must invert normalized values back to original within floating point tolerance."""
    revin = RevINNormalizer(num_features=3, eps=1e-5, affine=True)
    df = pd.DataFrame({
        "close": [100.0, 105.0, 110.0, 108.0, 115.0],
        "volume": [1000.0, 1500.0, 1200.0, 1800.0, 2000.0],
        "rsi": [35.0, 45.0, 55.0, 52.0, 68.0],
    })

    norm_df = revin.fit_transform(df)
    assert norm_df.shape == df.shape
    # Check mean is roughly 0 and std is roughly 1
    assert abs(norm_df["close"].mean()) < 1e-4

    # Exact inverse
    inv_df = revin.inverse_transform(norm_df)
    assert np.allclose(df.values, inv_df.values, atol=1e-5)


def test_revin_sliding_windows_no_lookahead():
    """Sliding windows must be strictly causal without accessing future bars."""
    revin = RevINNormalizer(num_features=2)
    df = pd.DataFrame({
        "f1": np.linspace(10, 100, 50),
        "f2": np.linspace(100, 10, 50),
    })

    windows, stats = revin.create_sliding_windows(df, feature_cols=["f1", "f2"], window_size=10, stride=2)
    assert len(windows) == (50 - 10) // 2 + 1
    assert windows.shape == (21, 10, 2)
    assert len(stats) == 21
    assert stats[0]["start_idx"] == 0
    assert stats[0]["end_idx"] == 9


# =====================================================================
# 2. DPO PREFERENCE ENGINE & DATA FLYWHEEL TESTS
# =====================================================================

class MockPosition:
    def __init__(self, pos_id, symbol, pnl, entry=100.0, sl=98.0, qty=1.0):
        self.position_id = pos_id
        self.symbol = symbol
        self.realized_pnl = pnl
        self.entry_price = entry
        self.stop_loss = sl
        self.quantity = qty
        self.exit_reason = "TAKE_PROFIT" if pnl > 0 else "STOP_LOSS"


def test_preference_engine_record_and_flywheel(tmp_path):
    test_db = str(tmp_path / "test_pref.db")
    pe = PreferenceEngine(db_path=test_db)

    # Ingest winning trade (Chosen)
    p_win = MockPosition("pos_win_1", "BTC/USDT", pnl=50.0)
    ok_win = pe.record_trade_outcome(p_win, context="TRENDING_BULL")
    assert ok_win is True

    # Ingest losing trade (Rejected)
    p_loss = MockPosition("pos_loss_1", "ETH/USDT", pnl=-20.0)
    ok_loss = pe.record_trade_outcome(p_loss, context="HIGH_VOLATILITY")
    assert ok_loss is True

    stats = pe.get_flywheel_stats()
    assert stats["total_preference_pairs"] == 2
    assert stats["chosen_count"] == 1
    assert stats["rejected_count"] == 1
    assert stats["win_rate_pct"] == 50.0


# =====================================================================
# 3. DPO SIGNAL GATE TESTS
# =====================================================================

def test_dpo_signal_gate_evaluation(tmp_path):
    test_db = str(tmp_path / "test_dpo_gate.db")
    pe = PreferenceEngine(db_path=test_db)

    # Seed 25 trades to enable training
    for i in range(15):
        pe.record_trade_outcome(MockPosition(f"win_{i}", "SOL/USDT", pnl=30.0 + i))
    for i in range(15):
        pe.record_trade_outcome(MockPosition(f"loss_{i}", "DOGE/USDT", pnl=-15.0 - i))

    gate = DPOSignalGate(preference_engine=pe, min_samples_to_train=20)
    assert gate.is_trained is True

    # Good candidate signal
    good_sig = Signal(
        symbol="SOL/USDT",
        strategy="r10_rsi_divergence",
        direction=SignalDirection.LONG,
        entry_price=150.0,
        stop_price=147.0,
        take_profit=159.0,
        score=78.0,
        opportunity_score=72.0,
        timestamp=datetime.now(timezone.utc),
        metadata={"signal_score": 78.0, "opportunity_score": 72.0},
    )
    decision = gate.evaluate_signal(good_sig, context="TRENDING_BULL")
    assert isinstance(decision.approved, bool)
    assert 0.0 <= decision.preference_score <= 1.0
    assert len(decision.feature_contributions) > 0


# =====================================================================
# 4. EVALUATION HARNESS TESTS
# =====================================================================

def test_evaluation_harness_metrics_and_splits():
    harness = EvaluationHarness(min_trades=10)

    # 15 wins of $20, 10 losses of $10 interleaved
    returns = [20.0, -10.0] * 10 + [20.0] * 5
    metrics = harness.evaluate_returns(returns)

    assert metrics.total_trades == 25
    assert metrics.win_rate_pct == 60.0
    assert metrics.profit_factor == 3.0
    assert metrics.expectancy == 8.0
    assert metrics.passed_validation is True
    assert 0.0 <= metrics.deflated_sharpe_ratio <= 1.0

    # Purged & Embargoed K-Fold Splits
    splits = harness.create_purged_kfold_splits(100, n_splits=5, embargo_pct=0.02)
    assert len(splits) == 5
    for train_idx, test_idx in splits:
        # Verify no overlap between train and test
        assert len(np.intersect1d(train_idx, test_idx)) == 0
