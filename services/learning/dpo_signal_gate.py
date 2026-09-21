"""
DPO (Direct Preference Optimization) Signal Gate for KRIPTO AGENT.
Acts as a predictive filter that screens candidate trading signals using
preferences learned from historical winning (Chosen) vs losing (Rejected) trades.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, precision_score, recall_score, roc_auc_score
from sklearn.preprocessing import StandardScaler

from services.learning.preference_engine import PreferenceEngine
from services.learning.revin_normalizer import RevINNormalizer
from shared.logging import get_logger

logger = get_logger("dpo-signal-gate", service="learning")


@dataclass
class DPOSignalDecision:
    approved: bool
    preference_score: float
    threshold: float
    reason: str
    feature_contributions: Dict[str, float]


class DPOSignalGate:
    """
    Evaluates candidate signals before order creation.
    Computes P(Chosen | Market Features) using preference-aligned classification.
    """

    FEATURE_KEYS = [
        "signal_score",
        "opportunity_score",
        "expected_rr",
        "stop_loss_dist_pct",
        "take_profit_dist_pct",
    ]

    def __init__(
        self,
        preference_engine: Optional[PreferenceEngine] = None,
        min_samples_to_train: int = 20,
        default_threshold: float = 0.35,
        enabled: bool = True,
    ):
        self.preference_engine = preference_engine or PreferenceEngine()
        self.min_samples_to_train = min_samples_to_train
        self.default_threshold = default_threshold
        self.enabled = enabled

        self.model: Optional[LogisticRegression] = None
        self.scaler: StandardScaler = StandardScaler()
        self.revin: RevINNormalizer = RevINNormalizer(num_features=len(self.FEATURE_KEYS))
        self.is_trained: bool = False
        self.last_trained_at: Optional[str] = None
        self.total_evaluations: int = 0
        self.total_rejected: int = 0
        self.evaluation_metrics: Dict[str, Any] = {
            "train_accuracy": None,
            "val_accuracy": None,
            "oos_accuracy": None,
            "oos_precision": None,
            "oos_recall": None,
            "oos_roc_auc": None,
            "sample_count": 0,
            "train_samples": 0,
            "val_samples": 0,
            "oos_samples": 0,
            "temporal_split_ratio": "70/15/15",
            "status": "UNINITIALIZED",
        }

        # Attempt initial train on startup
        self.train()

    def _extract_vector_from_signal(self, signal: Any) -> np.ndarray:
        """Constructs canonical feature vector from a Signal object."""
        meta = getattr(signal, "metadata", {}) or {}
        sig_score = float(getattr(signal, "score", None) or meta.get("signal_score", 50.0))
        opp_score = float(getattr(signal, "opportunity_score", None) or meta.get("opportunity_score", 50.0))

        entry_p = float(getattr(signal, "entry_price", 1.0) or 1.0)
        stop_p = float(getattr(signal, "stop_price", entry_p * 0.98) or (entry_p * 0.98))
        tp_p = float(getattr(signal, "take_profit", entry_p * 1.03) or (entry_p * 1.03))

        stop_dist = abs(entry_p - stop_p) / (entry_p + 1e-8)
        tp_dist = abs(tp_p - entry_p) / (entry_p + 1e-8)
        expected_rr = tp_dist / (stop_dist + 1e-8)

        vec = [sig_score, opp_score, expected_rr, stop_dist, tp_dist]
        return np.array(vec, dtype=np.float64)

    def train(self) -> bool:
        """
        Pulls preference pairs from database and fits the preference classifier
        using a strictly temporal 70% Train / 15% Validation / 15% Out-of-Sample split.
        Zero data leakage: Normalizers and scalers are fitted strictly on the train set.
        """
        try:
            with self.preference_engine._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT features_json, outcome_label, r_multiple
                    FROM dpo_preference_pairs
                    ORDER BY timestamp ASC, id ASC
                    LIMIT 2000
                    """
                )
                rows = cursor.fetchall()

            if len(rows) < self.min_samples_to_train:
                logger.info(
                    f"DPOSignalGate: Not enough preference samples ({len(rows)}/{self.min_samples_to_train}). Gate in pass-through mode."
                )
                self.is_trained = False
                self.evaluation_metrics = {
                    "train_accuracy": None,
                    "val_accuracy": None,
                    "oos_accuracy": None,
                    "oos_precision": None,
                    "oos_recall": None,
                    "oos_roc_auc": None,
                    "sample_count": len(rows),
                    "train_samples": 0,
                    "val_samples": 0,
                    "oos_samples": 0,
                    "temporal_split_ratio": "70/15/15",
                    "status": "INSUFFICIENT_SAMPLES",
                }
                return False

            X_list = []
            y_list = []
            for r in rows:
                feat = json.loads(r["features_json"])
                sig_s = float(feat.get("signal_score", 50.0))
                opp_s = float(feat.get("opportunity_score", 50.0))
                sl_pct = abs(float(feat.get("stop_loss_pct", 0.02)))
                tp_pct = abs(float(feat.get("take_profit_pct", 0.04)))
                rr = tp_pct / (sl_pct + 1e-8)

                X_list.append([sig_s, opp_s, rr, sl_pct, tp_pct])
                y_list.append(int(r["outcome_label"]))

            X = np.array(X_list, dtype=np.float64)
            y = np.array(y_list, dtype=np.int32)
            n_total = len(X)

            # Temporal Chronological Split (Zero random shuffle, strictly chronological)
            n_train = max(1, int(n_total * 0.70))
            n_val = max(0, int(n_total * 0.15))
            n_oos = n_total - n_train - n_val

            X_train, y_train = X[:n_train], y[:n_train]
            X_val, y_val = X[n_train : n_train + n_val], y[n_train : n_train + n_val]
            X_oos, y_oos = X[n_train + n_val :], y[n_train + n_val :]

            # Fit RevIN and Scaler ONLY on train set to eliminate look-ahead leakage
            X_train_norm = self.revin.fit_transform(X_train)
            X_train_scaled = self.scaler.fit_transform(X_train_norm)

            # Transform Val and OOS using train statistics
            if len(X_val) > 0:
                X_val_norm = (X_val - self.revin.last_mean) / (self.revin.last_stdev + 1e-8)
                X_val_scaled = self.scaler.transform(X_val_norm)
            else:
                X_val_scaled = np.empty((0, len(self.FEATURE_KEYS)))

            if len(X_oos) > 0:
                X_oos_norm = (X_oos - self.revin.last_mean) / (self.revin.last_stdev + 1e-8)
                X_oos_scaled = self.scaler.transform(X_oos_norm)
            else:
                X_oos_scaled = np.empty((0, len(self.FEATURE_KEYS)))

            # Fit classifier on training set
            clf = LogisticRegression(class_weight="balanced", C=1.0, random_state=42)
            clf.fit(X_train_scaled, y_train)

            # Calculate rigorous performance metrics across splits
            train_preds = clf.predict(X_train_scaled)
            train_acc = float(accuracy_score(y_train, train_preds))

            val_acc = (
                float(accuracy_score(y_val, clf.predict(X_val_scaled)))
                if len(y_val) > 0
                else train_acc
            )

            if len(y_oos) > 0:
                oos_preds = clf.predict(X_oos_scaled)
                oos_acc = float(accuracy_score(y_oos, oos_preds))
                oos_prec = float(precision_score(y_oos, oos_preds, zero_division=0))
                oos_rec = float(recall_score(y_oos, oos_preds, zero_division=0))
                oos_auc = None
                if len(np.unique(y_oos)) > 1:
                    try:
                        oos_probs = clf.predict_proba(X_oos_scaled)[:, 1]
                        oos_auc = float(roc_auc_score(y_oos, oos_probs))
                    except Exception:
                        oos_auc = None
            else:
                oos_acc = val_acc
                oos_prec = 0.0
                oos_rec = 0.0
                oos_auc = None

            self.evaluation_metrics = {
                "train_accuracy": round(train_acc, 4),
                "val_accuracy": round(val_acc, 4),
                "oos_accuracy": round(oos_acc, 4),
                "oos_precision": round(oos_prec, 4),
                "oos_recall": round(oos_rec, 4),
                "oos_roc_auc": round(oos_auc, 4) if oos_auc is not None else None,
                "sample_count": n_total,
                "train_samples": len(y_train),
                "val_samples": len(y_val),
                "oos_samples": len(y_oos),
                "temporal_split_ratio": "70/15/15",
                "status": "VALIDATED_OOS" if (n_total >= 30 and oos_acc >= 0.50) else "PAPER_COLLECTING",
            }

            self.model = clf
            self.is_trained = True
            self.last_trained_at = datetime.now(timezone.utc).isoformat()
            logger.info(
                f"DPOSignalGate: Successfully trained on {n_total} preference pairs. "
                f"Train Acc: {train_acc:.2f}, Val Acc: {val_acc:.2f}, OOS Acc: {oos_acc:.2f}"
            )
            return True
        except Exception as e:
            logger.error(f"DPOSignalGate training failed: {e}")
            self.is_trained = False
            return False

    def evaluate_signal(
        self,
        signal: Any,
        context: str = "UNKNOWN",
        threshold_override: Optional[float] = None,
    ) -> DPOSignalDecision:
        """
        Evaluates a candidate signal. Returns approval decision, preference score, and rationale.
        """
        self.total_evaluations += 1

        if not self.enabled:
            return DPOSignalDecision(
                approved=True,
                preference_score=1.0,
                threshold=0.0,
                reason="DPO Gate Disabled",
                feature_contributions={},
            )

        if not self.is_trained or self.model is None:
            # Fallback: neutral pass-through
            return DPOSignalDecision(
                approved=True,
                preference_score=0.50,
                threshold=self.default_threshold,
                reason="DPO Gate warming up (Pass-Through)",
                feature_contributions={},
            )

        try:
            raw_vec = self._extract_vector_from_signal(signal).reshape(1, -1)
            # RevIN & Scaler transform
            norm_vec = (raw_vec - self.revin.last_mean) / (self.revin.last_stdev + 1e-8)
            scaled_vec = self.scaler.transform(norm_vec)

            # Predict probability of being "Chosen" (label = 1)
            probs = self.model.predict_proba(scaled_vec)[0]
            prob_chosen = float(probs[1]) if len(probs) > 1 else float(probs[0])

            # Dynamic threshold adjustment by regime if desired
            threshold = threshold_override if threshold_override is not None else self.default_threshold
            if context == "HIGH_VOLATILITY":
                threshold = max(threshold, 0.45)  # Stricter filter during wild swings

            is_validated = self.evaluation_metrics.get("status") == "VALIDATED_OOS"
            if not is_validated:
                # Model is in PAPER_COLLECTING mode (unvalidated OOS accuracy); observe and record score without blocking trades
                is_approved = True
                reason = f"COLLECTING_DPO_PREFERENCE_GATE: Data collection mode (score {prob_chosen:.2f}, status {self.evaluation_metrics.get('status')})"
            else:
                is_approved = prob_chosen >= threshold

            feature_contribs = {}
            if hasattr(self.model, "coef_"):
                coefs = self.model.coef_[0]
                for idx, name in enumerate(self.FEATURE_KEYS):
                    feature_contribs[name] = round(float(scaled_vec[0, idx] * coefs[idx]), 3)

            if is_validated and not is_approved:
                self.total_rejected += 1
                reason = (
                    f"REJECTED_DPO_PREFERENCE_GATE: Signal probability of success ({prob_chosen:.2f}) "
                    f"is below threshold ({threshold:.2f}). Resembles historical stop-out pattern."
                )
            elif is_validated:
                reason = f"APPROVED_DPO_PREFERENCE_GATE: Preference score ({prob_chosen:.2f} >= {threshold:.2f})"

            return DPOSignalDecision(
                approved=is_approved,
                preference_score=round(prob_chosen, 3),
                threshold=round(threshold, 3),
                reason=reason,
                feature_contributions=feature_contribs,
            )
        except Exception as e:
            logger.warning(f"Error evaluating signal in DPOSignalGate: {e}. Safe fallback to approved.")
            return DPOSignalDecision(
                approved=True,
                preference_score=0.50,
                threshold=self.default_threshold,
                reason=f"DPO Gate evaluation exception: {e}",
                feature_contributions={},
            )

    def get_metrics(self) -> Dict[str, Any]:
        """Provides operational metrics for monitoring API and dashboard."""
        return {
            "enabled": self.enabled,
            "is_trained": self.is_trained,
            "last_trained_at": self.last_trained_at,
            "default_threshold": self.default_threshold,
            "total_evaluations": self.total_evaluations,
            "total_rejected": self.total_rejected,
            "rejection_rate_pct": round(
                (self.total_rejected / self.total_evaluations * 100.0)
                if self.total_evaluations > 0
                else 0.0,
                2,
            ),
            "evaluation_metrics": self.evaluation_metrics,
            "flywheel_stats": self.preference_engine.get_flywheel_stats(),
        }


# Authoritative Global Singleton
dpo_signal_gate = DPOSignalGate()
