"""
Financial DPO Preference Engine & Data Flywheel for KRIPTO AGENT.
Extracts, structures, and persists (Market State, Chosen, Rejected) preference pairs
from closed trades, continuously feeding the DPO Signal Gate.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Any, Dict, List, Optional, Tuple
import numpy as np

from shared.logging import get_logger

logger = get_logger("preference-engine", service="learning")
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = str(PROJECT_ROOT / "kripto_agent.db")


class PreferenceEngine:
    """
    Automated Data Flywheel & Preference Storage for Direct Preference Optimization (DPO).
    Pairs winning trades (Chosen) with losing trades (Rejected) to fine-tune signal gating.
    """

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Initializes the dpo_preference_pairs schema in SQLite."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS dpo_preference_pairs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    pair_id TEXT UNIQUE NOT NULL,
                    symbol TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    context TEXT NOT NULL,
                    features_json TEXT NOT NULL,
                    chosen_action TEXT NOT NULL,
                    rejected_action TEXT NOT NULL,
                    realized_pnl REAL NOT NULL,
                    r_multiple REAL NOT NULL,
                    outcome_label INTEGER NOT NULL,
                    exit_reason TEXT,
                    created_at TEXT NOT NULL
                );
                """
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_dpo_symbol ON dpo_preference_pairs(symbol);"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_dpo_label ON dpo_preference_pairs(outcome_label);"
            )
            cursor.execute(
                "CREATE INDEX IF NOT EXISTS idx_dpo_context ON dpo_preference_pairs(context);"
            )
            conn.commit()

    def record_trade_outcome(
        self,
        position: Any,
        features: Optional[Dict[str, float]] = None,
        context: str = "UNKNOWN",
    ) -> bool:
        """
        Ingests a newly closed position into the DPO preference dataset.
        """
        try:
            symbol = getattr(position, "symbol", "UNKNOWN")
            pos_id = getattr(position, "position_id", f"pos_{datetime.now(timezone.utc).timestamp()}")
            realized_pnl = float(getattr(position, "realized_pnl", 0.0) or 0.0)
            entry_price = float(getattr(position, "entry_price", 1.0) or 1.0)
            stop_loss = float(getattr(position, "stop_loss", entry_price * 0.98) or (entry_price * 0.98))
            quantity = float(getattr(position, "quantity", 1.0) or 1.0)
            exit_reason = str(getattr(position, "exit_reason", "") or "")

            # Calculate R-multiple
            risk_amount = abs(entry_price - stop_loss) * quantity
            if risk_amount <= 0.01:
                risk_amount = entry_price * quantity * 0.01
            r_multiple = realized_pnl / risk_amount

            # Chosen vs Rejected labeling
            is_chosen = realized_pnl > 0.0 or r_multiple > 0.5
            outcome_label = 1 if is_chosen else 0

            chosen_action = "BUY_HOLD_TO_PROFIT" if is_chosen else "AVOID_OR_TIGHT_SL"
            rejected_action = "AVOID_ENTRY" if is_chosen else "BUY_AT_MARKET"

            feat_payload = features or {
                "r_multiple": r_multiple,
                "realized_pnl": realized_pnl,
                "entry_price": entry_price,
            }

            now_str = datetime.now(timezone.utc).isoformat()
            pair_id = f"dpo_{pos_id}"

            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO dpo_preference_pairs (
                        pair_id, symbol, timestamp, context, features_json,
                        chosen_action, rejected_action, realized_pnl, r_multiple,
                        outcome_label, exit_reason, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        pair_id,
                        symbol,
                        now_str,
                        context,
                        json.dumps(feat_payload),
                        chosen_action,
                        rejected_action,
                        realized_pnl,
                        r_multiple,
                        outcome_label,
                        exit_reason,
                        now_str,
                    ),
                )
                conn.commit()

            logger.info(
                f"DPO Flywheel Ingested: {symbol} | Label={outcome_label} (Pnl=${realized_pnl:.2f}, R={r_multiple:.2f}) -> {pair_id}"
            )
            return True
        except Exception as e:
            logger.error(f"Failed to record trade outcome in DPO preference engine: {e}")
            return False

    def backfill_from_historical_positions(self) -> int:
        """
        Parses all existing closed positions in paper_positions and fills dpo_preference_pairs.
        """
        inserted = 0
        try:
            with self._get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT position_id, symbol, side, quantity, entry_price, current_price,
                           stop_loss, take_profit, realized_pnl, closed_at, entry_context, r_multiple
                    FROM paper_positions
                    WHERE status = 'CLOSED'
                    """
                )
                rows = cursor.fetchall()
                now_str = datetime.now(timezone.utc).isoformat()

                for r in rows:
                    pos_id = r["position_id"]
                    symbol = r["symbol"]
                    realized_pnl = float(r["realized_pnl"] or 0.0)
                    entry_price = float(r["entry_price"] or 1.0)
                    stop_loss = float(r["stop_loss"] or (entry_price * 0.98))
                    quantity = float(r["quantity"] or 1.0)
                    closed_at = str(r["closed_at"] or now_str)
                    context = str(r["entry_context"] or "HISTORICAL_REPLAY")

                    r_mult = r["r_multiple"]
                    if r_mult is not None:
                        r_multiple = float(r_mult)
                    else:
                        risk_amount = abs(entry_price - stop_loss) * quantity
                        if risk_amount <= 0.01:
                            risk_amount = entry_price * quantity * 0.01
                        r_multiple = realized_pnl / risk_amount

                    is_chosen = realized_pnl > 0.0 or r_multiple > 0.5
                    outcome_label = 1 if is_chosen else 0
                    chosen_action = "BUY_HOLD_TO_PROFIT" if is_chosen else "AVOID_OR_TIGHT_SL"
                    rejected_action = "AVOID_ENTRY" if is_chosen else "BUY_AT_MARKET"
                    exit_reason = "PROFIT_TARGET" if is_chosen else "STOP_LOSS_OR_CLOSE"

                    # Basic feature vector reconstruct
                    feat = {
                        "entry_price": entry_price,
                        "stop_loss_pct": (stop_loss - entry_price) / (entry_price + 1e-8),
                        "take_profit_pct": ((float(r["take_profit"] or entry_price * 1.03)) - entry_price) / (entry_price + 1e-8),
                        "quantity": quantity,
                        "realized_pnl": realized_pnl,
                        "r_multiple": r_multiple,
                    }

                    pair_id = f"dpo_hist_{pos_id}"
                    cursor.execute(
                        """
                        INSERT OR IGNORE INTO dpo_preference_pairs (
                            pair_id, symbol, timestamp, context, features_json,
                            chosen_action, rejected_action, realized_pnl, r_multiple,
                            outcome_label, exit_reason, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            pair_id,
                            symbol,
                            closed_at,
                            "HISTORICAL_REPLAY",
                            json.dumps(feat),
                            chosen_action,
                            rejected_action,
                            realized_pnl,
                            r_multiple,
                            outcome_label,
                            exit_reason,
                            now_str,
                        ),
                    )
                    if cursor.rowcount > 0:
                        inserted += 1

                conn.commit()
            logger.info(f"DPO Flywheel backfill complete: {inserted} historical pairs loaded.")
            return inserted
        except Exception as e:
            logger.error(f"Error backfilling historical preference pairs: {e}")
            return inserted

    def get_training_dataset(self, limit: int = 2000) -> Tuple[np.ndarray, np.ndarray, List[str]]:
        """
        Extracts feature matrix X and label vector y for training DPO gate.
        Returns (X, y, feature_names).
        """
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT features_json, outcome_label
                FROM dpo_preference_pairs
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = cursor.fetchall()

        if not rows:
            return np.empty((0, 4)), np.empty(0), []

        feature_names = ["entry_price", "stop_loss_pct", "take_profit_pct", "quantity"]
        X_list = []
        y_list = []

        for r in rows:
            try:
                feat = json.loads(r["features_json"])
                vec = [float(feat.get(k, 0.0)) for k in feature_names]
                X_list.append(vec)
                y_list.append(int(r["outcome_label"]))
            except Exception:
                continue

        return np.array(X_list), np.array(y_list), feature_names

    def get_flywheel_stats(self) -> Dict[str, Any]:
        """Returns real-time Data Flywheel & Preference metrics."""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT count(*) FROM dpo_preference_pairs")
            total = cursor.fetchone()[0]

            cursor.execute("SELECT count(*) FROM dpo_preference_pairs WHERE outcome_label = 1")
            chosen = cursor.fetchone()[0]

            cursor.execute("SELECT count(*) FROM dpo_preference_pairs WHERE outcome_label = 0")
            rejected = cursor.fetchone()[0]

            cursor.execute("SELECT avg(r_multiple), avg(realized_pnl) FROM dpo_preference_pairs")
            row = cursor.fetchone()
            avg_r = float(row[0] or 0.0)
            avg_pnl = float(row[1] or 0.0)

        win_rate = (chosen / total * 100.0) if total > 0 else 0.0
        return {
            "total_preference_pairs": total,
            "chosen_count": chosen,
            "rejected_count": rejected,
            "win_rate_pct": round(win_rate, 2),
            "avg_r_multiple": round(avg_r, 3),
            "avg_realized_pnl": round(avg_pnl, 2),
        }
