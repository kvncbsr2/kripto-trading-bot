"""
Constrained CatBoost Signal Gate Model
======================================
Implements low-complexity, regularized CatBoost classifier.
- Shallow trees (depth <= 4)
- L2 leaf regularization
- Early stopping on validation split
- Pre-defined, audited hyperparameter search budget
- Zero test leakage: final evaluation strictly on untouched OOS
"""

import json
import logging
from typing import Dict, Any, List, Tuple, Optional
import numpy as np
from catboost import CatBoostClassifier
from sklearn.metrics import accuracy_score, precision_score, recall_score, roc_auc_score, brier_score_loss

from services.learning.calibrator import ProbabilityCalibrator
from shared.logging import get_logger

logger = get_logger("catboost-gate", service="learning")


class ConstrainedCatBoostGate:
    """
    Constrained complexity CatBoost gate with hyperparameter tuning budget,
    early stopping, and post-hoc probability calibration.
    """

    HYPERPARAMETER_BUDGET: List[Dict[str, Any]] = [
        {"config_id": "cfg_01_baseline", "depth": 3, "l2_leaf_reg": 5.0, "learning_rate": 0.03, "iterations": 150},
        {"config_id": "cfg_02_shallow", "depth": 2, "l2_leaf_reg": 5.0, "learning_rate": 0.03, "iterations": 150},
        {"config_id": "cfg_03_depth4", "depth": 4, "l2_leaf_reg": 5.0, "learning_rate": 0.03, "iterations": 150},
        {"config_id": "cfg_04_high_reg", "depth": 3, "l2_leaf_reg": 7.0, "learning_rate": 0.03, "iterations": 150},
        {"config_id": "cfg_05_low_reg", "depth": 3, "l2_leaf_reg": 3.0, "learning_rate": 0.03, "iterations": 150},
        {"config_id": "cfg_06_fast_shallow", "depth": 2, "l2_leaf_reg": 7.0, "learning_rate": 0.05, "iterations": 100},
    ]

    def __init__(self, model_id: str = "catboost_v1_constrained"):
        self.model_id = model_id
        self.best_config: Optional[Dict[str, Any]] = None
        self.best_model: Optional[CatBoostClassifier] = None
        self.calibrator: Optional[ProbabilityCalibrator] = None
        self.search_history: List[Dict[str, Any]] = []
        self.is_trained: bool = False
        self.tuned_threshold: float = 0.50
        self.evaluation_metrics: Dict[str, Any] = {}

    def fit_with_budget(
        self,
        X_train: np.ndarray,
        y_train: np.ndarray,
        X_val: np.ndarray,
        y_val: np.ndarray,
        feature_names: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """
        Explores the pre-defined hyperparameter budget strictly on the validation split.
        Logs every attempted configuration.
        """
        self.search_history = []
        best_val_score = -1.0
        best_cfg = None
        best_clf = None

        y_train = y_train.astype(int)
        y_val = y_val.astype(int)

        for cfg in self.HYPERPARAMETER_BUDGET:
            clf = CatBoostClassifier(
                iterations=cfg["iterations"],
                depth=cfg["depth"],
                learning_rate=cfg["learning_rate"],
                l2_leaf_reg=cfg["l2_leaf_reg"],
                random_seed=42,
                verbose=False,
                early_stopping_rounds=15,
                eval_metric="Logloss",
            )
            clf.fit(
                X_train,
                y_train,
                eval_set=(X_val, y_val),
                verbose=False,
            )

            val_preds_prob = clf.predict_proba(X_val)[:, 1]
            val_preds_bin = (val_preds_prob >= 0.50).astype(int)
            val_acc = float(accuracy_score(y_val, val_preds_bin))
            val_brier = float(brier_score_loss(y_val, val_preds_prob))

            record = {
                "config": cfg,
                "best_iteration": int(clf.get_best_iteration() or cfg["iterations"]),
                "val_accuracy": round(val_acc, 4),
                "val_brier": round(val_brier, 4),
            }
            self.search_history.append(record)

            # Optimization criterion: Validation accuracy with Brier tie-breaker
            score = val_acc - (val_brier * 0.1)
            if score > best_val_score:
                best_val_score = score
                best_cfg = cfg
                best_clf = clf

        self.best_config = best_cfg
        self.best_model = best_clf
        self.is_trained = True

        # Fit probability calibrator strictly on out-of-fold validation set predictions
        val_probs = self.best_model.predict_proba(X_val)[:, 1]
        self.calibrator = ProbabilityCalibrator(method="sigmoid").fit(val_probs, y_val)

        # Optimize decision threshold on calibrated validation probabilities (F1 / Balanced)
        cal_val_probs = self.calibrator.predict_proba(val_probs)
        best_th = 0.50
        best_th_score = -1.0
        for th in np.linspace(0.35, 0.65, 31):
            preds = (cal_val_probs >= th).astype(int)
            acc = accuracy_score(y_val, preds)
            if acc > best_th_score:
                best_th_score = acc
                best_th = round(float(th), 3)

        self.tuned_threshold = best_th

        logger.info(
            f"CatBoost Grid Search Complete. Best Config: {best_cfg['config_id']} "
            f"(depth={best_cfg['depth']}, l2={best_cfg['l2_leaf_reg']}) | "
            f"Val Acc={record['val_accuracy']}, Tuned Threshold={self.tuned_threshold}"
        )

        return {
            "best_config": self.best_config,
            "search_history": self.search_history,
            "tuned_threshold": self.tuned_threshold,
        }

    def predict_raw_proba(self, X: np.ndarray) -> np.ndarray:
        if not self.is_trained or self.best_model is None:
            return np.full(len(X), 0.50)
        return self.best_model.predict_proba(X)[:, 1]

    def predict_calibrated_proba(self, X: np.ndarray) -> np.ndarray:
        raw_probs = self.predict_raw_proba(X)
        if self.calibrator is None:
            return raw_probs
        return self.calibrator.predict_proba(raw_probs)

    def evaluate_oos(
        self,
        X_oos: np.ndarray,
        y_oos: np.ndarray,
    ) -> Dict[str, Any]:
        """
        Evaluates the trained & calibrated CatBoost model strictly on the untouched OOS split.
        Computes both uncalibrated and calibrated metrics.
        """
        y_oos = y_oos.astype(int)
        raw_probs = self.predict_raw_proba(X_oos)
        cal_probs = self.predict_calibrated_proba(X_oos)

        raw_preds = (raw_probs >= 0.50).astype(int)
        cal_preds = (cal_probs >= self.tuned_threshold).astype(int)

        # Metrics uncalibrated
        raw_acc = float(accuracy_score(y_oos, raw_preds))
        raw_brier = float(brier_score_loss(y_oos, raw_probs))

        # Metrics calibrated
        cal_acc = float(accuracy_score(y_oos, cal_preds))
        cal_prec = float(precision_score(y_oos, cal_preds, zero_division=0))
        cal_rec = float(recall_score(y_oos, cal_preds, zero_division=0))
        cal_brier = float(brier_score_loss(y_oos, cal_probs))

        try:
            cal_auc = float(roc_auc_score(y_oos, cal_probs))
        except Exception:
            cal_auc = 0.50

        cal_eval = self.calibrator.evaluate_metrics(y_oos, cal_probs) if self.calibrator else {}

        self.evaluation_metrics = {
            "model_id": self.model_id,
            "best_config": self.best_config,
            "tuned_threshold": self.tuned_threshold,
            "uncalibrated": {
                "accuracy": round(raw_acc, 4),
                "brier_score": round(raw_brier, 4),
            },
            "calibrated": {
                "accuracy": round(cal_acc, 4),
                "precision": round(cal_prec, 4),
                "recall": round(cal_rec, 4),
                "roc_auc": round(cal_auc, 4),
                "brier_score": round(cal_brier, 4),
                "log_loss": cal_eval.get("log_loss", None),
                "ece": cal_eval.get("ece", None),
                "reliability_bins": cal_eval.get("reliability_bins", []),
            },
        }

        return self.evaluation_metrics
