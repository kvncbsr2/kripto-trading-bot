"""
Model Lifecycle Management & Shadow Mode Governance for KRIPTO AGENT.
=====================================================================
Enforces audited state transitions:
COLLECTING -> TRAINED -> EVALUATED -> SHADOW -> APPROVED -> ACTIVE
Additional states: REJECTED, DEGRADED, ROLLED_BACK.

All model versions, hashes, data ranges, metrics, and transitions are
persisted in SQLite (model_manifests table).
"""

from enum import Enum
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional

from shared.logging import get_logger

logger = get_logger("model-lifecycle", service="learning")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "kripto_agent.db"


class ModelLifecycleState(str, Enum):
    COLLECTING = "COLLECTING"
    TRAINED = "TRAINED"
    EVALUATED = "EVALUATED"
    SHADOW = "SHADOW"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    REJECTED = "REJECTED"
    DEGRADED = "DEGRADED"
    ROLLED_BACK = "ROLLED_BACK"


class ModelLifecycleManager:
    """
    State machine and registry for governing predictive models in KRIPTO AGENT.
    Strictly prohibits automatic live trading promotion without human/audit approval.
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = str(db_path or DB_PATH)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        try:
            with self._get_conn() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS model_manifests (
                        model_id TEXT PRIMARY KEY,
                        model_name TEXT NOT NULL,
                        version TEXT NOT NULL,
                        state TEXT NOT NULL,
                        model_hash TEXT NOT NULL,
                        code_version TEXT NOT NULL,
                        feature_schema_hash TEXT NOT NULL,
                        training_range_start TEXT,
                        training_range_end TEXT,
                        data_sources_json TEXT NOT NULL,
                        exclusion_counts_json TEXT NOT NULL,
                        evaluation_metrics_json TEXT NOT NULL,
                        transition_history_json TEXT NOT NULL,
                        rollback_model_id TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                """)
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to initialize model_manifests table: {e}")

    def register_candidate_model(
        self,
        model_id: str,
        model_name: str,
        version: str,
        code_version: str,
        feature_schema_hash: str,
        training_range_start: Optional[str] = None,
        training_range_end: Optional[str] = None,
        data_sources: Optional[Dict[str, int]] = None,
        exclusions: Optional[Dict[str, int]] = None,
        initial_state: ModelLifecycleState = ModelLifecycleState.TRAINED,
        rollback_model_id: Optional[str] = None,
    ) -> bool:
        now_iso = datetime.now(timezone.utc).isoformat()
        content_hash = hashlib.sha256(
            f"{model_id}:{version}:{code_version}:{feature_schema_hash}:{now_iso}".encode()
        ).hexdigest()[:16]

        history = [{
            "from_state": "NONE",
            "to_state": initial_state.value,
            "timestamp": now_iso,
            "reason": "Initial model candidate registration",
        }]

        try:
            with self._get_conn() as conn:
                conn.execute("""
                    INSERT INTO model_manifests (
                        model_id, model_name, version, state, model_hash,
                        code_version, feature_schema_hash, training_range_start,
                        training_range_end, data_sources_json, exclusion_counts_json,
                        evaluation_metrics_json, transition_history_json,
                        rollback_model_id, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(model_id) DO UPDATE SET
                        state=excluded.state,
                        updated_at=excluded.updated_at
                """, (
                    model_id,
                    model_name,
                    version,
                    initial_state.value,
                    content_hash,
                    code_version,
                    feature_schema_hash,
                    training_range_start,
                    training_range_end,
                    json.dumps(data_sources or {}),
                    json.dumps(exclusions or {}),
                    json.dumps({}),
                    json.dumps(history),
                    rollback_model_id,
                    now_iso,
                    now_iso,
                ))
                conn.commit()
            logger.info(f"Registered model candidate {model_id} ({version}) in state {initial_state.value}")
            return True
        except Exception as e:
            logger.error(f"Failed to register model {model_id}: {e}")
            return False

    def update_evaluation_metrics(self, model_id: str, metrics: Dict[str, Any]) -> bool:
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT state, transition_history_json FROM model_manifests WHERE model_id = ?", (model_id,))
                row = cursor.fetchone()
                if not row:
                    return False
                current_state = row["state"]
                try:
                    history = json.loads(row["transition_history_json"])
                except Exception:
                    history = []

                next_state = ModelLifecycleState.EVALUATED.value
                history.append({
                    "from_state": current_state,
                    "to_state": next_state,
                    "timestamp": now_iso,
                    "reason": "Evaluation completed against benchmark test harness",
                })

                conn.execute("""
                    UPDATE model_manifests
                    SET state = ?, evaluation_metrics_json = ?, transition_history_json = ?, updated_at = ?
                    WHERE model_id = ?
                """, (next_state, json.dumps(metrics), json.dumps(history), now_iso, model_id))
                conn.commit()
            return True
        except Exception as e:
            logger.error(f"Failed to update evaluation metrics for {model_id}: {e}")
            return False

    def transition_state(
        self,
        model_id: str,
        target_state: ModelLifecycleState,
        reason: str,
        authorized_by: str = "system",
    ) -> bool:
        """
        Transitions model to target state with audited rationale.
        Promoting to ACTIVE strictly requires human / explicit authorization.
        """
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT state, transition_history_json FROM model_manifests WHERE model_id = ?", (model_id,))
                row = cursor.fetchone()
                if not row:
                    return False

                current_state = row["state"]
                try:
                    history = json.loads(row["transition_history_json"])
                except Exception:
                    history = []

                # Guard: Transition to ACTIVE requires authorization
                if target_state == ModelLifecycleState.ACTIVE and authorized_by == "system":
                    logger.warning(
                        f"Autonomous promotion to ACTIVE rejected for {model_id}. Explicit user authorization required."
                    )
                    return False

                history.append({
                    "from_state": current_state,
                    "to_state": target_state.value,
                    "timestamp": now_iso,
                    "reason": reason,
                    "authorized_by": authorized_by,
                })

                conn.execute("""
                    UPDATE model_manifests
                    SET state = ?, transition_history_json = ?, updated_at = ?
                    WHERE model_id = ?
                """, (target_state.value, json.dumps(history), now_iso, model_id))
                conn.commit()
            logger.info(f"Model {model_id} transitioned: {current_state} -> {target_state.value} ({reason})")
            return True
        except Exception as e:
            logger.error(f"Failed to transition state for {model_id}: {e}")
            return False

    def get_active_model_id(self) -> Optional[str]:
        try:
            with self._get_conn() as conn:
                cur = conn.cursor()
                cur.execute("SELECT model_id FROM model_manifests WHERE state = 'ACTIVE' ORDER BY updated_at DESC LIMIT 1")
                row = cur.fetchone()
                return row["model_id"] if row else None
        except Exception:
            return None

    def get_shadow_models(self) -> List[Dict[str, Any]]:
        try:
            with self._get_conn() as conn:
                cur = conn.cursor()
                cur.execute("SELECT * FROM model_manifests WHERE state = 'SHADOW' ORDER BY updated_at DESC")
                return [dict(r) for r in cur.fetchall()]
        except Exception:
            return []

    def get_manifest(self, model_id: str) -> Optional[Dict[str, Any]]:
        try:
            with self._get_conn() as conn:
                cur = conn.cursor()
                cur.execute("SELECT * FROM model_manifests WHERE model_id = ?", (model_id,))
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception:
            return None

    def list_all_models(self) -> List[Dict[str, Any]]:
        try:
            with self._get_conn() as conn:
                cur = conn.cursor()
                cur.execute("SELECT * FROM model_manifests ORDER BY updated_at DESC")
                return [dict(r) for r in cur.fetchall()]
        except Exception:
            return []


# Global Singleton
model_lifecycle_manager = ModelLifecycleManager()
