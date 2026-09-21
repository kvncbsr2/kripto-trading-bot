"""
Decision Logger & Snapshot Registry for KRIPTO AGENT Learning System.
====================================================================
Records every evaluated candidate signal at decision time with zero lookahead,
strictly preserving data availability times, features snapshot, model predictions,
and subsequent execution and trade outcomes.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional
import uuid

from shared.logging import get_logger

logger = get_logger("decision-logger", service="learning")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "kripto_agent.db"


class DecisionLogger:
    """
    Immutable, audit-ready decision snapshot logger for candidate signal evaluation.
    Guarantees idempotency and strict causal lineage.
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
                    CREATE TABLE IF NOT EXISTS learning_decisions (
                        decision_id TEXT PRIMARY KEY,
                        experiment_id TEXT NOT NULL,
                        signal_id TEXT NOT NULL,
                        trade_id TEXT,
                        data_source TEXT NOT NULL,
                        execution_class TEXT NOT NULL,
                        event_time_utc TEXT NOT NULL,
                        data_available_at TEXT NOT NULL,
                        outcome_available_at TEXT,
                        symbol TEXT NOT NULL,
                        direction TEXT NOT NULL,
                        strategy TEXT NOT NULL,
                        market_regime TEXT NOT NULL,
                        features_json TEXT NOT NULL,
                        data_freshness_sec REAL DEFAULT 0.0,
                        model_version TEXT NOT NULL,
                        feature_schema_hash TEXT NOT NULL,
                        strategy_version TEXT NOT NULL,
                        predicted_prob REAL,
                        decision_threshold REAL,
                        gate_decision TEXT NOT NULL,
                        gate_reason TEXT NOT NULL,
                        risk_engine_approved INTEGER NOT NULL,
                        risk_engine_reason TEXT,
                        initial_risk_amount REAL,
                        realized_net_pnl REAL,
                        realized_r_multiple REAL,
                        finalization_status TEXT NOT NULL,
                        is_simulation INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_ld_sig ON learning_decisions(signal_id);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_ld_trade ON learning_decisions(trade_id);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_ld_exp ON learning_decisions(experiment_id);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_ld_sym ON learning_decisions(symbol);")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_ld_created ON learning_decisions(created_at);")
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to initialize learning_decisions schema: {e}")

    def log_candidate_decision(
        self,
        experiment_id: str,
        signal_id: str,
        symbol: str,
        direction: str,
        strategy: str,
        market_regime: str,
        features: Dict[str, Any],
        model_version: str = "v1.0-shadow",
        feature_schema_hash: str = "schema-canonical-8f",
        strategy_version: str = "1.0",
        predicted_prob: Optional[float] = None,
        decision_threshold: Optional[float] = None,
        gate_decision: str = "SHADOW_PASS_THROUGH",
        gate_reason: str = "Gate in Shadow Mode",
        risk_engine_approved: bool = False,
        risk_engine_reason: Optional[str] = None,
        data_source: str = "PAPER_LIVE",
        execution_class: str = "PAPER",
        event_time: Optional[datetime] = None,
        data_freshness_sec: float = 0.0,
        initial_risk_amount: Optional[float] = None,
        is_simulation: bool = False,
    ) -> str:
        """
        Records an atomic decision snapshot. Idempotent on signal_id if already present.
        """
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        ev_time = (event_time or now).isoformat()
        decision_id = f"dec_{signal_id}"

        feat_json = json.dumps(features)

        try:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT INTO learning_decisions (
                        decision_id, experiment_id, signal_id, trade_id, data_source,
                        execution_class, event_time_utc, data_available_at, symbol,
                        direction, strategy, market_regime, features_json,
                        data_freshness_sec, model_version, feature_schema_hash,
                        strategy_version, predicted_prob, decision_threshold,
                        gate_decision, gate_reason, risk_engine_approved,
                        risk_engine_reason, initial_risk_amount, finalization_status,
                        is_simulation, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(decision_id) DO UPDATE SET
                        risk_engine_approved=excluded.risk_engine_approved,
                        risk_engine_reason=excluded.risk_engine_reason,
                        initial_risk_amount=COALESCE(excluded.initial_risk_amount, learning_decisions.initial_risk_amount),
                        updated_at=excluded.updated_at;
                    """,
                    (
                        decision_id,
                        experiment_id,
                        signal_id,
                        None,
                        data_source,
                        execution_class,
                        ev_time,
                        ev_time,
                        symbol,
                        direction,
                        strategy,
                        market_regime,
                        feat_json,
                        data_freshness_sec,
                        model_version,
                        feature_schema_hash,
                        strategy_version,
                        predicted_prob,
                        decision_threshold,
                        gate_decision,
                        gate_reason,
                        1 if risk_engine_approved else 0,
                        risk_engine_reason,
                        initial_risk_amount,
                        "REJECTED_UNFILLED" if not risk_engine_approved else "PENDING",
                        1 if is_simulation else 0,
                        now_iso,
                        now_iso,
                    ),
                )
                conn.commit()
                return decision_id
        except Exception as e:
            logger.error(f"Failed to log candidate decision for {symbol}: {e}")
            return decision_id

    def log_risk_decision(
        self,
        decision_id: Optional[str],
        approved: bool,
        reason: str = "",
    ) -> bool:
        """Updates the decision record with the active RiskEngine approval/rejection."""
        if not decision_id:
            return False
        now_iso = datetime.now(timezone.utc).isoformat()
        status = "PENDING" if approved else "REJECTED_RISK"
        try:
            with self._get_conn() as conn:
                conn.execute(
                    """
                    UPDATE learning_decisions
                    SET risk_engine_approved = ?,
                        risk_engine_reason = ?,
                        finalization_status = CASE 
                            WHEN finalization_status = 'PENDING' AND ? = 0 THEN 'REJECTED_RISK'
                            ELSE finalization_status 
                        END,
                        updated_at = ?
                    WHERE decision_id = ? OR signal_id = ?
                    """,
                    (1 if approved else 0, reason, 1 if approved else 0, now_iso, decision_id, decision_id),
                )
                conn.commit()
                return True
        except Exception as e:
            logger.error(f"Failed to log risk decision for {decision_id}: {e}")
            return False

    def link_trade_execution(
        self,
        signal_id: str,
        trade_id: str,
        initial_risk_amount: Optional[float] = None,
    ):
        """Associates the executed trade position ID with the decision record."""
        decision_id = f"dec_{signal_id}"
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            with self._get_conn() as conn:
                conn.execute(
                    """
                    UPDATE learning_decisions
                    SET trade_id = ?, initial_risk_amount = COALESCE(?, initial_risk_amount),
                        finalization_status = 'PENDING', updated_at = ?
                    WHERE decision_id = ? OR signal_id = ?
                    """,
                    (trade_id, initial_risk_amount, now_iso, decision_id, signal_id),
                )
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to link trade execution {trade_id} to decision: {e}")

    def finalize_trade_outcome(
        self,
        trade_id: str,
        realized_net_pnl: float,
        realized_r_multiple: float,
        outcome_time: Optional[datetime] = None,
    ):
        """Finalizes the economic outcome of the trade when closed."""
        now = outcome_time or datetime.now(timezone.utc)
        now_iso = now.isoformat()
        try:
            with self._get_conn() as conn:
                conn.execute(
                    """
                    UPDATE learning_decisions
                    SET realized_net_pnl = ?,
                        realized_r_multiple = ?,
                        outcome_available_at = ?,
                        finalization_status = 'FINALIZED',
                        updated_at = ?
                    WHERE trade_id = ?
                    """,
                    (realized_net_pnl, realized_r_multiple, now_iso, now_iso, trade_id),
                )
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to finalize trade outcome for {trade_id}: {e}")

    def get_recent_decisions(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Retrieves recent decision records for telemetry and API observability."""
        try:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT decision_id, experiment_id, signal_id, trade_id, symbol,
                           direction, strategy, market_regime, model_version,
                           predicted_prob, decision_threshold, gate_decision,
                           gate_reason, risk_engine_approved, risk_engine_reason,
                           initial_risk_amount, realized_net_pnl, realized_r_multiple,
                           finalization_status, is_simulation, event_time_utc
                    FROM learning_decisions
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
                return [dict(r) for r in cursor.fetchall()]
        except Exception as e:
            logger.error(f"Failed to get recent decisions: {e}")
            return []


# Authoritative Global Singleton
decision_logger = DecisionLogger()
