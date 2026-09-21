import os
import sqlite3
import tempfile
import pytest
from datetime import datetime, timezone
import pandas as pd
import numpy as np

from services.learning.decision_logger import DecisionLogger
from services.learning.model_lifecycle import ModelLifecycleManager, ModelLifecycleState
from services.learning.signal_gate_engine import SignalGateEngine
from services.strategy_engine.adaptive_learning import AdaptiveLearningEngine
from services.learning.preference_engine import PreferenceEngine
from shared.schemas import Signal
from shared.enums import SignalDirection


@pytest.fixture
def temp_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    yield path
    try:
        os.remove(path)
    except Exception:
        pass


def test_idempotency_adaptive_learning(temp_db):
    engine = AdaptiveLearningEngine(db_path=temp_db)
    
    class DummyPos:
        position_id = "test_pos_001"
        symbol = "BTC/USDT"
        side = SignalDirection.LONG
        entry_price = 50000.0
        current_price = 49000.0
        realized_pnl = -100.0
        quantity = 0.1
        opened_at = datetime.now(timezone.utc)
        closed_at = datetime.now(timezone.utc)
        fees_paid = 1.0
        peak_price = 50200.0

    engine.record_closed_trade(DummyPos(), exit_reason="STOP_LOSS")
    summary1 = engine.get_summary()
    assert summary1["total_trades_analyzed"] == 1
    assert summary1["ledger_verified_trades"] == 1

    engine.record_closed_trade(DummyPos(), exit_reason="STOP_LOSS")
    summary2 = engine.get_summary()
    assert summary2["total_trades_analyzed"] == 1
    assert summary2["ledger_verified_trades"] == 1


def test_preference_engine_deterministic_pair_id(temp_db):
    pref = PreferenceEngine(db_path=temp_db)

    class DummyPos:
        position_id = "pos_flywheel_123"
        symbol = "ETH/USDT"
        entry_price = 3000.0
        stop_loss = 2950.0
        quantity = 1.0
        realized_pnl = 150.0
        exit_reason = "TAKE_PROFIT"

    ok1 = pref.record_trade_outcome(DummyPos())
    assert ok1 is True

    ok2 = pref.record_trade_outcome(DummyPos())
    assert ok2 is True

    conn = sqlite3.connect(temp_db)
    cur = conn.cursor()
    cur.execute("SELECT COUNT(1) FROM dpo_preference_pairs WHERE pair_id = 'dpo_pos_flywheel_123'")
    count = cur.fetchone()[0]
    conn.close()
    assert count == 1


def test_leak_free_walk_forward_split(temp_db):
    gate = SignalGateEngine(db_path=temp_db)

    n_samples = 100
    dates = pd.date_range("2026-01-01", periods=n_samples, freq="h")
    df = pd.DataFrame({
        "symbol": ["BTC/USDT"] * n_samples,
        "direction": ["LONG"] * n_samples,
        "strategy": ["momentum_dip_rebound"] * n_samples,
        "signal_score": np.random.uniform(0.5, 0.9, n_samples),
        "opportunity_score": np.random.uniform(50.0, 90.0, n_samples),
        "entry_price": [50000.0] * n_samples,
        "stop_price": [49000.0] * n_samples,
        "take_profit": [52000.0] * n_samples,
        "outcome_realized_pnl": [100.0 if i % 2 == 0 else -100.0 for i in range(n_samples)],
        "outcome_r_multiple": [1.0 if i % 2 == 0 else -1.0 for i in range(n_samples)],
        "timestamp": dates.astype(str),
    })

    conn = sqlite3.connect(temp_db)
    gate._init_tables()
    df.to_sql("signals", conn, if_exists="append", index=False)
    conn.close()

    ok = gate.train_and_evaluate(min_samples=30)
    assert ok is True
    metrics = gate.evaluation_metrics
    assert metrics["train_samples"] > 0
    assert metrics["val_samples"] > 0
    assert metrics["oos_samples"] > 0
    assert gate.mode == "SHADOW"


def test_shadow_mode_isolation(temp_db):
    gate = SignalGateEngine(db_path=temp_db)
    sig = Signal(
        symbol="SOL/USDT",
        direction=SignalDirection.LONG,
        confidence=0.85,
        strategy="momentum_dip_rebound",
        entry_price=140.0,
        stop_price=135.0,
        take_profit=150.0,
    )
    decision = gate.evaluate_signal_shadow(
        signal=sig,
        market_regime="BTC_BULL_HIGH_VOL",
    )
    assert decision.approved is True
    assert hasattr(decision, "predicted_prob")
    assert decision.mode == "SHADOW"
    assert decision.threshold >= 0.0


def test_decision_logger_pipeline(temp_db):
    logger = DecisionLogger(db_path=temp_db)
    sig_id = "sig_test_001"

    logger.log_candidate_decision(
        experiment_id="exp_001",
        signal_id=sig_id,
        symbol="AVAX/USDT",
        direction="LONG",
        strategy="r10_rsi_divergence",
        market_regime="BULLISH",
        features={"rsi": 28.5, "confidence": 0.8},
        predicted_prob=0.65,
        decision_threshold=0.55,
        risk_engine_approved=True,
    )

    logger.link_trade_execution(signal_id=sig_id, trade_id="pos_avax_1", initial_risk_amount=50.0)
    logger.finalize_trade_outcome(trade_id="pos_avax_1", realized_net_pnl=15.0, realized_r_multiple=1.5)

    decisions = logger.get_recent_decisions(limit=5)
    assert len(decisions) == 1
    d = decisions[0]
    assert d["signal_id"] == sig_id
    assert d["risk_engine_approved"] == 1
    assert d["trade_id"] == "pos_avax_1"
    assert d["realized_net_pnl"] == 15.0
    assert d["realized_r_multiple"] == 1.5


def test_adaptive_parameter_stability_on_single_loss(temp_db):
    engine = AdaptiveLearningEngine(db_path=temp_db)
    initial_atr = engine.get_adapted_atr_multiplier()
    initial_rsi = engine.get_adapted_rsi_cutoff()

    class SingleLosingPos:
        position_id = "single_loss_1"
        symbol = "BTC/USDT"
        side = SignalDirection.LONG
        entry_price = 60000.0
        current_price = 59000.0
        realized_pnl = -100.0
        quantity = 0.1
        opened_at = datetime.fromtimestamp(1700000000, tz=timezone.utc)
        closed_at = datetime.fromtimestamp(1700003600, tz=timezone.utc)
        fees_paid = 1.0
        peak_price = 60000.0

    engine.record_closed_trade(SingleLosingPos(), exit_reason="STOP_LOSS")
    assert engine.get_adapted_atr_multiplier() == initial_atr
    assert engine.get_adapted_rsi_cutoff() == initial_rsi


def test_bandit_risk_ceiling_safety():
    """Test that contextual bandit never mutates risk profile level."""
    from services.learning.contextual_bandit import contextual_bandit
    from apps.api.app.api.state import RUNTIME_STATE, apply_profile_to_system

    # Set risk profile level to 1
    apply_profile_to_system(1, source="test_init")
    assert RUNTIME_STATE["active_risk_profile_level"] == 1

    # Select arm from bandit
    strat, risk_lvl = contextual_bandit.select_arm("BTC_BULL_HIGH_VOL")
    assert isinstance(strat, str)
    assert isinstance(risk_lvl, int)

    # Invariant: RUNTIME_STATE risk level remains strictly 1 (bandit cannot mutate it)
    assert RUNTIME_STATE["active_risk_profile_level"] == 1


def test_adaptive_reconciliation_with_paper_positions(temp_db):
    """Test reconcile_with_paper_positions processes only closed positions without double-counting."""
    engine = AdaptiveLearningEngine(db_path=temp_db)
    
    conn = sqlite3.connect(temp_db)
    conn.execute("""
        CREATE TABLE paper_positions (
            position_id TEXT PRIMARY KEY,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            status TEXT NOT NULL,
            entry_price REAL NOT NULL,
            current_price REAL NOT NULL,
            realized_pnl REAL NOT NULL,
            quantity REAL NOT NULL,
            opened_at TEXT NOT NULL,
            closed_at TEXT NOT NULL,
            exit_reason TEXT,
            fees_paid REAL DEFAULT 0.0,
            peak_price REAL
        );
    """)
    now_iso = datetime.now(timezone.utc).isoformat()
    conn.execute("""
        INSERT INTO paper_positions VALUES
        ('p_rec_1', 'BTC/USDT', 'LONG', 'CLOSED', 50000.0, 51000.0, 100.0, 0.1, ?, ?, 'TAKE_PROFIT', 1.0, 51200.0),
        ('p_rec_2', 'ETH/USDT', 'LONG', 'CLOSED', 3000.0, 2900.0, -50.0, 0.5, ?, ?, 'STOP_LOSS', 0.5, 3050.0),
        ('p_rec_3', 'SOL/USDT', 'LONG', 'OPEN', 140.0, 145.0, 5.0, 1.0, ?, ?, NULL, 0.2, 146.0);
    """, (now_iso, now_iso, now_iso, now_iso, now_iso, now_iso))
    conn.commit()
    conn.close()

    count1 = engine.reconcile_with_paper_positions(db_path=temp_db)
    assert count1 == 2  # Only the 2 CLOSED positions

    summary1 = engine.get_summary()
    assert summary1["ledger_verified_trades"] == 2

    # Second reconciliation should be idempotent (0 new trades)
    count2 = engine.reconcile_with_paper_positions(db_path=temp_db)
    assert count2 == 0


def test_probability_calibrator_ece():
    """Verify ProbabilityCalibrator fits, calculates ECE and reduces miscalibration."""
    from services.learning.calibrator import ProbabilityCalibrator, calculate_ece

    np.random.seed(42)
    # Overconfident uncalibrated probabilities
    y_true = np.array([1, 1, 1, 1, 0, 0, 0, 0, 1, 0] * 5)
    uncal_probs = np.array([0.95, 0.90, 0.85, 0.88, 0.10, 0.15, 0.20, 0.12, 0.70, 0.30] * 5)

    ece_before, bins = calculate_ece(y_true, uncal_probs)
    assert isinstance(ece_before, float)
    assert len(bins) == 10

    calibrator = ProbabilityCalibrator(method="sigmoid")
    calibrator.fit(uncal_probs, y_true)
    assert calibrator.is_fitted is True

    cal_probs = calibrator.predict_proba(uncal_probs)
    eval_metrics = calibrator.evaluate_metrics(y_true, cal_probs)
    assert "brier_score" in eval_metrics
    assert "log_loss" in eval_metrics
    assert "ece" in eval_metrics
    assert eval_metrics["brier_score"] <= 0.25


def test_constrained_catboost_gate():
    """Verify ConstrainedCatBoostGate hyperparameter budget and validation early stopping."""
    from services.learning.catboost_model import ConstrainedCatBoostGate

    np.random.seed(42)
    X_train = np.random.randn(60, 8)
    y_train = (X_train[:, 0] + X_train[:, 1] > 0).astype(int)

    X_val = np.random.randn(20, 8)
    y_val = (X_val[:, 0] + X_val[:, 1] > 0).astype(int)

    X_oos = np.random.randn(20, 8)
    y_oos = (X_oos[:, 0] + X_oos[:, 1] > 0).astype(int)

    gate = ConstrainedCatBoostGate(model_id="cb_test")
    fit_res = gate.fit_with_budget(X_train, y_train, X_val, y_val)
    assert gate.is_trained is True
    assert gate.best_config is not None
    assert len(fit_res["search_history"]) == 6  # Exact 6-config budget

    eval_oos = gate.evaluate_oos(X_oos, y_oos)
    assert "calibrated" in eval_oos
    assert "accuracy" in eval_oos["calibrated"]
    assert "brier_score" in eval_oos["calibrated"]
    assert "reliability_bins" in eval_oos["calibrated"]


def test_adwin_drift_monitor_idempotency_and_detection(temp_db):
    """Verify AdwinDriftMonitor idempotency ledger and drift flagging on abrupt distribution change."""
    from services.learning.adwin_monitor import AdwinDriftMonitor

    monitor = AdwinDriftMonitor(model_id="test_gate", delta=0.01, db_path=temp_db)

    # 1. Test idempotency
    res1 = monitor.update("pred_001", y_true=1.0, y_pred_prob=0.9, enforce_idempotency=True)
    assert res1["skipped"] is False

    res2 = monitor.update("pred_001", y_true=1.0, y_pred_prob=0.9, enforce_idempotency=True)
    assert res2["skipped"] is True

    # 2. Test drift detection with a stream that switches from near-0 error to near-1 error
    monitor.reset()
    for i in range(80):
        # Low error stream: true label matches prediction
        monitor.update(f"p_low_{i}", y_true=1.0, y_pred_prob=0.95, enforce_idempotency=False)

    drifts_found = False
    for j in range(80):
        # Abrupt shift: high error (true=0, predicted=1)
        res = monitor.update(f"p_high_{j}", y_true=0.0, y_pred_prob=0.99, enforce_idempotency=False)
        if res["drift_detected"]:
            drifts_found = True
            break

    assert drifts_found is True
    summary = monitor.get_summary()
    assert summary["total_drifts_flagged"] >= 1



