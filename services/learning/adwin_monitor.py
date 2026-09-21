"""
ADWIN Drift Monitor for Signal Gate Models
==========================================
Monitors prediction error streams and probability calibration drift online
using Adaptive Windowing (river.drift.ADWIN).

INVARIANTS & SAFETY:
- STRICTLY OBSERVATIONAL: Zero authority to place trades, close positions,
  or alter risk profiles.
- IDEMPOTENT: Replay and online processing are deduplicated via SQLite ledgers.
- BOUNDED METRIC: Tracks squared prediction error (Brier loss per sample) in [0, 1].
"""

import sqlite3
import datetime
from typing import Dict, Any, List, Optional, Tuple

try:
    from river.drift import ADWIN
except ImportError:
    ADWIN = None

from shared.logging import get_logger

logger = get_logger("adwin-monitor", service="learning")

DEFAULT_DB_PATH = "kripto_agent.db"


class AdwinDriftMonitor:
    """
    Online drift monitor wrapping River's ADWIN algorithm.
    Tracks sample-level squared prediction errors (y_true - p_hat)^2.
    When distribution drift is detected, records an audit event without altering trading execution.
    """

    def __init__(
        self,
        model_id: str = "signal_gate_default",
        delta: float = 0.002,
        db_path: str = DEFAULT_DB_PATH,
        auto_warmup: bool = True,
    ):
        self.model_id = model_id
        self.delta = delta
        self.db_path = db_path
        self.adwin = ADWIN(delta=delta)
        self.sample_count = 0
        self.drift_count = 0
        self.history: List[Dict[str, Any]] = []
        self._init_db()
        if auto_warmup:
            self.warmup_from_db()

    def warmup_from_db(self, limit: int = 500):
        """
        Replays the most recent recorded prediction errors from SQLite to restore
        ADWIN's internal window state and variance estimates across service restarts.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT error_value FROM adwin_processed_predictions
                    WHERE model_id = ?
                    ORDER BY processed_at ASC
                    LIMIT ?
                    """,
                    (self.model_id, limit),
                )
                rows = cursor.fetchall()
                if rows:
                    for (err,) in rows:
                        self.adwin.update(float(err))
                        self.sample_count += 1
                    logger.info(f"AdwinDriftMonitor ({self.model_id}) restored with {len(rows)} historical records.")
        except Exception as e:
            logger.error(f"Failed to warm up ADWIN monitor from DB: {e}")

    def _init_db(self):
        """Creates SQLite audit tables if not existing."""
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS adwin_drift_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    model_id TEXT NOT NULL,
                    event_timestamp TEXT NOT NULL,
                    sample_index INTEGER NOT NULL,
                    window_size INTEGER NOT NULL,
                    window_mean REAL NOT NULL,
                    error_value REAL NOT NULL,
                    drift_detected INTEGER NOT NULL,
                    notes TEXT
                )
                """
            )
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS adwin_processed_predictions (
                    prediction_id TEXT PRIMARY KEY,
                    model_id TEXT NOT NULL,
                    processed_at TEXT NOT NULL,
                    error_value REAL NOT NULL
                )
                """
            )
            conn.commit()

    def reset(self):
        """Resets the in-memory ADWIN window."""
        self.adwin = ADWIN(delta=self.delta)
        self.sample_count = 0
        self.drift_count = 0
        self.history = []

    def update(
        self,
        prediction_id: str,
        y_true: float,
        y_pred_prob: float,
        timestamp: Optional[str] = None,
        notes: str = "",
        enforce_idempotency: bool = True,
    ) -> Dict[str, Any]:
        """
        Updates the ADWIN monitor with a single verified outcome.
        Metric: Squared error (y_true - y_pred_prob)^2 in [0, 1].

        Returns a dictionary containing drift status and current window metrics.
        """
        if timestamp is None:
            timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()

        # Check idempotency ledger
        if enforce_idempotency:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT prediction_id FROM adwin_processed_predictions WHERE prediction_id = ?",
                    (prediction_id,),
                )
                if cursor.fetchone():
                    logger.debug(f"[ADWIN] Skipping already processed prediction_id: {prediction_id}")
                    return {
                        "prediction_id": prediction_id,
                        "skipped": True,
                        "drift_detected": False,
                        "sample_count": self.sample_count,
                    }

        # Calculate Brier error element
        y_true_clean = 1.0 if y_true > 0.5 else 0.0
        p_clean = max(0.0, min(1.0, float(y_pred_prob)))
        error_value = float((y_true_clean - p_clean) ** 2)

        # Feed error to ADWIN
        self.adwin.update(error_value)
        self.sample_count += 1
        drift_detected = bool(self.adwin.drift_detected)

        current_mean = float(self.adwin.mean) if hasattr(self.adwin, "mean") else 0.0
        current_width = int(self.adwin.width) if hasattr(self.adwin, "width") else 0

        if drift_detected:
            self.drift_count += 1
            logger.warning(
                f"[ADWIN-DRIFT-ALERT] Concept drift detected in '{self.model_id}' at sample #{self.sample_count}! "
                f"Window Mean Error: {current_mean:.4f}, Window Width: {current_width}, Current Sample Error: {error_value:.4f}"
            )

        # Persist event
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            if enforce_idempotency:
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO adwin_processed_predictions 
                    (prediction_id, model_id, processed_at, error_value)
                    VALUES (?, ?, ?, ?)
                    """,
                    (prediction_id, self.model_id, timestamp, error_value),
                )
            if drift_detected:
                cursor.execute(
                    """
                    INSERT INTO adwin_drift_events
                    (model_id, event_timestamp, sample_index, window_size, window_mean, error_value, drift_detected, notes)
                    VALUES (?, ?, ?, ?, ?, ?, 1, ?)
                    """,
                    (self.model_id, timestamp, self.sample_count, current_width, current_mean, error_value, notes),
                )
            conn.commit()

        result = {
            "prediction_id": prediction_id,
            "skipped": False,
            "sample_index": self.sample_count,
            "error_value": round(error_value, 4),
            "window_mean_error": round(current_mean, 4),
            "window_size": current_width,
            "drift_detected": drift_detected,
            "total_drifts": self.drift_count,
        }
        self.history.append(result)
        return result

    def get_summary(self) -> Dict[str, Any]:
        """Returns current operational status of the monitor."""
        return {
            "model_id": self.model_id,
            "delta": self.delta,
            "total_samples_observed": self.sample_count,
            "total_drifts_flagged": self.drift_count,
            "current_window_size": int(self.adwin.width) if hasattr(self.adwin, "width") else 0,
            "current_window_mean_error": round(float(self.adwin.mean), 4) if hasattr(self.adwin, "mean") else 0.0,
        }

    @staticmethod
    def evaluate_alarm_quality(
        delta: float = 0.002,
        stationary_length: int = 200,
        drift_length: int = 100,
        noise_mean: float = 0.20,
        drift_mean: float = 0.80,
    ) -> Dict[str, Any]:
        """
        Audits ADWIN's statistical alarm quality across controlled stationary and drift regimes.
        Evaluates:
          - False Alarm Rate (FAR) under null hypothesis (stationary stream)
          - True Detection Rate (Sensitivity)
          - Detection Delay / Latency in steps after distribution shift
        """
        import numpy as np

        # Phase 1: Stationary Stream (H0: Mean = noise_mean)
        np.random.seed(42)
        stationary_errors = np.clip(np.random.normal(noise_mean, 0.05, stationary_length), 0.0, 1.0)

        test_adwin = ADWIN(delta=delta)
        false_alarms = 0
        for val in stationary_errors:
            test_adwin.update(float(val))
            if test_adwin.drift_detected:
                false_alarms += 1

        far = false_alarms / float(stationary_length)

        # Phase 2: Shift Stream (H1: Mean shifts abruptly to drift_mean)
        drift_errors = np.clip(np.random.normal(drift_mean, 0.05, drift_length), 0.0, 1.0)
        detected_step = None
        for step, val in enumerate(drift_errors, start=1):
            test_adwin.update(float(val))
            if test_adwin.drift_detected:
                detected_step = step
                break

        return {
            "delta_confidence": delta,
            "stationary_samples": stationary_length,
            "false_alarms_count": false_alarms,
            "false_alarm_rate": round(far, 4),
            "drift_samples": drift_length,
            "drift_detected": detected_step is not None,
            "detection_delay_steps": detected_step,
            "alarm_precision_status": "EXCELLENT (0 False Alarms)" if false_alarms == 0 else f"ELEVATED ({false_alarms} FA)",
        }

