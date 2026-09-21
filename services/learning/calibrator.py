"""
Calibrator Module for Signal Gate Predictions
=============================================
Implements Platt Scaling (Sigmoid) and Isotonic Regression.
Computes Brier Score, Log Loss, and Expected Calibration Error (ECE).
"""

import numpy as np
from typing import Dict, Any, Tuple, List, Optional
from sklearn.linear_model import LogisticRegression
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import brier_score_loss, log_loss


def calculate_ece(y_true: np.ndarray, y_prob: np.ndarray, n_bins: int = 10) -> Tuple[float, List[Dict[str, float]]]:
    """
    Calculates Expected Calibration Error (ECE) and reliability diagram bins.
    """
    bin_boundaries = np.linspace(0, 1, n_bins + 1)
    bin_lowers = bin_boundaries[:-1]
    bin_uppers = bin_boundaries[1:]

    ece = 0.0
    diagram_points = []
    n_samples = len(y_true)

    if n_samples == 0:
        return 0.0, []

    for bin_lower, bin_upper in zip(bin_lowers, bin_uppers):
        in_bin = (y_prob >= bin_lower) & (y_prob < bin_upper if bin_upper < 1.0 else y_prob <= bin_upper)
        prop_in_bin = np.mean(in_bin)

        if prop_in_bin > 0:
            accuracy_in_bin = np.mean(y_true[in_bin])
            confidence_in_bin = np.mean(y_prob[in_bin])
            ece += np.abs(accuracy_in_bin - confidence_in_bin) * prop_in_bin
            diagram_points.append({
                "bin_lower": float(bin_lower),
                "bin_upper": float(bin_upper),
                "confidence": float(confidence_in_bin),
                "accuracy": float(accuracy_in_bin),
                "count": int(np.sum(in_bin)),
            })
        else:
            diagram_points.append({
                "bin_lower": float(bin_lower),
                "bin_upper": float(bin_upper),
                "confidence": float((bin_lower + bin_upper) / 2.0),
                "accuracy": 0.0,
                "count": 0,
            })

    return float(ece), diagram_points


class ProbabilityCalibrator:
    """
    Post-hoc probability calibrator. Fits strictly on validation split predictions.
    Supports 'sigmoid' (Platt scaling) and 'isotonic'.
    """

    def __init__(self, method: str = "sigmoid"):
        self.method = method.lower()
        self.is_fitted = False
        self.calibrator = None
        self.val_brier_before: Optional[float] = None
        self.val_brier_after: Optional[float] = None
        self.val_ece_before: Optional[float] = None
        self.val_ece_after: Optional[float] = None

    def fit(self, val_probs: np.ndarray, val_labels: np.ndarray) -> "ProbabilityCalibrator":
        """
        Fits calibrator on out-of-fold validation probabilities and binary labels.
        """
        val_probs = np.clip(val_probs, 1e-6, 1.0 - 1e-6)
        val_labels = val_labels.astype(int)

        self.val_brier_before = float(brier_score_loss(val_labels, val_probs))
        ece_bef, _ = calculate_ece(val_labels, val_probs)
        self.val_ece_before = ece_bef

        if self.method == "isotonic":
            self.calibrator = IsotonicRegression(out_of_bounds="clip")
            self.calibrator.fit(val_probs, val_labels)
        else:
            # Platt Scaling via Logistic Regression on log-odds
            log_odds = np.log(val_probs / (1.0 - val_probs)).reshape(-1, 1)
            self.calibrator = LogisticRegression(C=1.0, solver="lbfgs")
            self.calibrator.fit(log_odds, val_labels)

        self.is_fitted = True
        cal_probs = self.predict_proba(val_probs)
        self.val_brier_after = float(brier_score_loss(val_labels, cal_probs))
        ece_aft, _ = calculate_ece(val_labels, cal_probs)
        self.val_ece_after = ece_aft

        return self

    def predict_proba(self, probs: np.ndarray) -> np.ndarray:
        """
        Transforms raw probabilities into calibrated probabilities.
        """
        if not self.is_fitted or self.calibrator is None:
            return probs

        probs = np.clip(probs, 1e-6, 1.0 - 1e-6)
        if self.method == "isotonic":
            return np.clip(self.calibrator.predict(probs), 0.0, 1.0)
        else:
            log_odds = np.log(probs / (1.0 - probs)).reshape(-1, 1)
            return np.clip(self.calibrator.predict_proba(log_odds)[:, 1], 0.0, 1.0)

    def evaluate_metrics(self, y_true: np.ndarray, y_prob: np.ndarray) -> Dict[str, Any]:
        """
        Evaluates Brier, Log Loss, and ECE with reliability curve coordinates.
        """
        y_prob = np.clip(y_prob, 1e-6, 1.0 - 1e-6)
        y_true = y_true.astype(int)

        brier = float(brier_score_loss(y_true, y_prob))
        ll = float(log_loss(y_true, y_prob))
        ece, bins = calculate_ece(y_true, y_prob)

        return {
            "brier_score": round(brier, 4),
            "log_loss": round(ll, 4),
            "ece": round(ece, 4),
            "reliability_bins": bins,
        }
