"""
Contextual Multi-Armed Bandit Engine (Thompson Sampling with Normal-Gamma Conjugate Priors)
=============================================================================================
Autonomous Bayesian Learner for KRIPTO AGENT:
- 25 Predefined Arms: 5 Core Strategies × 5 Distinct Risk Levels (L1, L3, L5, L7, L10)
- 6 Categorical Contexts: {BTC_BULL, BTC_BEAR, BTC_RANGING} × {LOW_VOL, HIGH_VOL}
- Continuous R-Multiple Reward Formulation with Normal-Gamma Posterior Updating
- Safety Bounds: Automatic 24-Hour Quarantine on Underperforming Arms (R < -0.5 across 10 trades)
- SQLite State Persistence (bandit_posteriors table)
"""

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import numpy as np

from shared.logging import add_system_log, get_logger

logger = get_logger("contextual-bandit", service="learning")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "kripto_agent.db"

# =============================================================================
# 1. ARMS SPECIFICATION (5 Strategies × 5 Risk Levels = 25 Arms)
# =============================================================================
STRATEGIES = [
    "bollinger_volume_breakout",
    "ema_macd_pullback",
    "momentum_dip_rebound",
    "r10_rsi_divergence",
    "regime_gated_pullback",
]

RISK_LEVELS = [1, 3, 5, 7, 10]

# Pre-defined 25 Arm Tuples and Identifiers
ARMS: List[Tuple[str, int]] = [
    (strat, lvl) for strat in STRATEGIES for lvl in RISK_LEVELS
]

ARM_IDS: List[str] = [f"{strat}__L{lvl}" for strat, lvl in ARMS]

# =============================================================================
# 2. CONTEXT SPECIFICATION (3 Macro BTC Trends × 2 Volatility States = 6 Contexts)
# =============================================================================
CONTEXTS = [
    "BTC_BULL_LOW_VOL",
    "BTC_BULL_HIGH_VOL",
    "BTC_BEAR_LOW_VOL",
    "BTC_BEAR_HIGH_VOL",
    "BTC_RANGING_LOW_VOL",
    "BTC_RANGING_HIGH_VOL",
]


class ContextualBandit:
    """
    Contextual Multi-Armed Bandit implementation using Bayesian Thompson Sampling.
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = str(db_path or DB_PATH)
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15.0)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """Initializes SQLite schema for bandit posteriors."""
        try:
            with self._get_conn() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS bandit_posteriors (
                        context TEXT NOT NULL,
                        arm_id TEXT NOT NULL,
                        strategy_id TEXT NOT NULL,
                        risk_level INTEGER NOT NULL,
                        mu REAL NOT NULL,
                        n REAL NOT NULL,
                        alpha REAL NOT NULL,
                        beta REAL NOT NULL,
                        total_trades INTEGER NOT NULL DEFAULT 0,
                        rolling_r_json TEXT NOT NULL DEFAULT '[]',
                        quarantined_until TEXT,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (context, arm_id)
                    );
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS bandit_processed_trades (
                        trade_id TEXT PRIMARY KEY,
                        context TEXT NOT NULL,
                        arm_id TEXT NOT NULL,
                        r_multiple REAL NOT NULL,
                        strategy TEXT,
                        processed_at TEXT NOT NULL
                    );
                """)
                conn.commit()
        except Exception as e:
            logger.error(f"Failed to initialize bandit tables: {e}")

    @staticmethod
    def arm_to_id(arm: Union[str, Tuple[str, int]]) -> str:
        if isinstance(arm, tuple):
            return f"{arm[0]}__L{arm[1]}"
        return str(arm)

    @staticmethod
    def id_to_arm(arm_id: str) -> Tuple[str, int]:
        parts = arm_id.split("__L")
        if len(parts) == 2:
            return parts[0], int(parts[1])
        return arm_id, 1

    def get_posterior(self, context: str, arm: Union[str, Tuple[str, int]]) -> Tuple[float, float, float, float]:
        """
        Retrieves (mu, n, alpha, beta) for (context, arm).
        Defaults to conservative Level 1 baseline if unrecorded.
        """
        arm_id = self.arm_to_id(arm)
        try:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT mu, n, alpha, beta FROM bandit_posteriors WHERE context = ? AND arm_id = ?",
                    (context, arm_id),
                )
                row = cursor.fetchone()
                if row:
                    return float(row["mu"]), float(row["n"]), float(row["alpha"]), float(row["beta"])
        except Exception as e:
            logger.debug(f"Error fetching posterior for {context} {arm_id}: {e}")

        # Safe Default: mu=0.0 (neutral), n=2.0, alpha=2.0, beta=2.0
        # If L1, give modest positive prior mu=0.1 to prefer capital preservation
        strat, lvl = self.id_to_arm(arm_id)
        default_mu = 0.15 if lvl == 1 else 0.0
        return default_mu, 2.0, 2.0, 2.0

    def save_posterior(
        self,
        context: str,
        arm: Union[str, Tuple[str, int]],
        mu: float,
        n: float,
        alpha: float,
        beta: float,
        r_multiple: Optional[float] = None,
        trade_id: Optional[str] = None,
        strategy: Optional[str] = None,
    ):
        """Saves updated posterior parameters to database and checks quarantine condition."""
        arm_id = self.arm_to_id(arm)
        strat, lvl = self.id_to_arm(arm_id)
        now_iso = datetime.now(timezone.utc).isoformat()

        try:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT total_trades, rolling_r_json, quarantined_until FROM bandit_posteriors WHERE context = ? AND arm_id = ?",
                    (context, arm_id),
                )
                row = cursor.fetchone()
                if row:
                    total_trades = (row["total_trades"] + 1) if r_multiple is not None else row["total_trades"]
                    try:
                        rolling_r = json.loads(row["rolling_r_json"])
                    except Exception:
                        rolling_r = []
                    quarantined_until = row["quarantined_until"]
                else:
                    total_trades = 1 if r_multiple is not None else 0
                    rolling_r = []
                    quarantined_until = None

                if r_multiple is not None:
                    rolling_r.append(round(float(r_multiple), 3))
                    if len(rolling_r) > 10:
                        rolling_r = rolling_r[-10:]

                # Check 24-Hour Quarantine Condition:
                # If >= 10 trades and average R < -0.5
                if len(rolling_r) >= 10:
                    avg_r = sum(rolling_r) / len(rolling_r)
                    if avg_r < -0.5:
                        quarantine_dt = datetime.now(timezone.utc) + timedelta(hours=24)
                        quarantined_until = quarantine_dt.isoformat()
                        q_msg = (
                            f"⛔ BANDIT KARANTİNA: Kol {arm_id} ({context}) ardışık 10 işlemde ortalama "
                            f"R={avg_r:.2f} (< -0.5) ürettiği için 24 saatliğine karantinaya alındı."
                        )
                        logger.warning(q_msg)
                        add_system_log(q_msg, level="WARNING", service="learning")
                        try:
                            from services.notification.telegram import telegram_service
                            import asyncio
                            asyncio.create_task(telegram_service.send_message(q_msg))
                        except Exception:
                            pass

                cursor.execute("""
                    INSERT INTO bandit_posteriors (
                        context, arm_id, strategy_id, risk_level, mu, n, alpha, beta,
                        total_trades, rolling_r_json, quarantined_until, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(context, arm_id) DO UPDATE SET
                        mu = excluded.mu,
                        n = excluded.n,
                        alpha = excluded.alpha,
                        beta = excluded.beta,
                        total_trades = excluded.total_trades,
                        rolling_r_json = excluded.rolling_r_json,
                        quarantined_until = excluded.quarantined_until,
                        updated_at = excluded.updated_at;
                """, (
                    context, arm_id, strat, lvl, mu, n, alpha, beta,
                    total_trades, json.dumps(rolling_r), quarantined_until, now_iso
                ))

                if trade_id:
                    cursor.execute("""
                        INSERT OR IGNORE INTO bandit_processed_trades (
                            trade_id, context, arm_id, r_multiple, strategy, processed_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                    """, (trade_id, context, arm_id, float(r_multiple or 0.0), strategy or strat, now_iso))

                conn.commit()
        except Exception as e:
            logger.error(f"Failed to persist bandit posterior: {e}")

    def is_arm_quarantined(self, context: str, arm: Union[str, Tuple[str, int]]) -> bool:
        """Checks whether the specified arm is currently quarantined for this context."""
        arm_id = self.arm_to_id(arm)
        try:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT quarantined_until FROM bandit_posteriors WHERE context = ? AND arm_id = ?",
                    (context, arm_id),
                )
                row = cursor.fetchone()
                if row and row["quarantined_until"]:
                    q_time = datetime.fromisoformat(row["quarantined_until"])
                    if q_time > datetime.now(timezone.utc):
                        return True
        except Exception:
            pass
        return False

    def is_arm_proven(self, context: str, arm: Union[str, Tuple[str, int]]) -> bool:
        """Returns True only if the arm has >= 30 recorded trades in this context."""
        arm_id = self.arm_to_id(arm)
        try:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT total_trades FROM bandit_posteriors WHERE context = ? AND arm_id = ?",
                    (context, arm_id),
                )
                row = cursor.fetchone()
                if row and row["total_trades"] is not None:
                    return int(row["total_trades"]) >= 30
        except Exception:
            pass
        return False

    def select_arm(self, context: str) -> Tuple[str, int]:
        """
        Samples a reward for every active non-quarantined arm via Thompson Sampling
        and selects the arm with the highest sampled expectation.
        Always returns a valid (strategy_id, risk_profile_level) tuple.
        """
        if context not in CONTEXTS:
            context = "BTC_RANGING_LOW_VOL"

        best_arm: Tuple[str, int] = ("momentum_dip_rebound", 1)
        best_sample = -float("inf")

        # Fetch all posteriors for this context in a single batch query
        arm_data = {}
        now_dt = datetime.now(timezone.utc)
        try:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT arm_id, mu, n, alpha, beta, quarantined_until FROM bandit_posteriors WHERE context = ?",
                    (context,),
                )
                for r in cursor.fetchall():
                    arm_data[r["arm_id"]] = (
                        float(r["mu"]),
                        float(r["n"]),
                        float(r["alpha"]),
                        float(r["beta"]),
                        r["quarantined_until"],
                    )
        except Exception as e:
            logger.debug(f"Error batch-fetching posteriors for context {context}: {e}")

        for arm in ARMS:
            strat, lvl = arm
            from services.strategy_engine.registry import STRATEGY_REGISTRY
            meta = STRATEGY_REGISTRY.get(strat)
            if meta and not meta.enabled:
                continue

            arm_id = self.arm_to_id(arm)
            data = arm_data.get(arm_id)
            if data:
                mu, n, alpha, beta, q_until = data
                if q_until:
                    try:
                        if datetime.fromisoformat(q_until) > now_dt:
                            continue
                    except Exception:
                        pass
            else:
                strat, lvl = self.id_to_arm(arm_id)
                mu = 0.15 if lvl == 1 else 0.0
                n, alpha, beta = 2.0, 2.0, 2.0

            # Normal-Gamma Thompson Sampling:
            # 1. Sample precision tau ~ Gamma(alpha, scale=1/beta)
            # 2. Sample mean mu_sample ~ Normal(mu, std=1/sqrt(n * tau))
            try:
                safe_alpha = max(0.1, alpha)
                safe_beta = max(0.1, beta)
                safe_n = max(0.1, n)

                tau_sample = np.random.gamma(safe_alpha, 1.0 / safe_beta)
                sigma_sample = 1.0 / np.sqrt(safe_n * tau_sample) if (safe_n * tau_sample) > 0 else 1e3
                sigma_sample = min(10.0, max(0.01, sigma_sample))
                mu_sample = float(np.random.normal(mu, sigma_sample))
            except Exception:
                mu_sample = mu

            if mu_sample > best_sample:
                best_sample = mu_sample
                best_arm = arm

        return best_arm

    def update_posterior(
        self,
        context: str,
        arm: Union[str, Tuple[str, int]],
        r_multiple: float,
        trade_id: Optional[str] = None,
        strategy: Optional[str] = None,
    ):
        """
        Performs Bayesian conjugate Normal-Gamma update after a position closes.
        Idempotent via trade_id tracking, and strictly excludes manual trades.
        """
        if context not in CONTEXTS:
            context = "BTC_RANGING_LOW_VOL"

        # Item 8: Exclude manual trades from bandit rewards to prevent learning distortion
        if strategy:
            strat_upper = str(strategy).upper()
            if strat_upper.startswith("MANUAL") or "MANUAL" in strat_upper:
                logger.info(f"Bandit ignoring manual trade {trade_id} ({strategy}) for Bayesian updates.")
                if trade_id:
                    try:
                        with self._get_conn() as conn:
                            conn.execute(
                                "INSERT OR IGNORE INTO bandit_processed_trades (trade_id, context, arm_id, r_multiple, strategy, processed_at) VALUES (?, ?, ?, ?, ?, ?)",
                                (trade_id, context, self.arm_to_id(arm), float(r_multiple), strategy, datetime.now(timezone.utc).isoformat())
                            )
                            conn.commit()
                    except Exception:
                        pass
                return

        # Item 8: Idempotency check with trade_id
        if trade_id:
            try:
                with self._get_conn() as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT trade_id FROM bandit_processed_trades WHERE trade_id = ?", (trade_id,))
                    if cur.fetchone():
                        logger.info(f"Bandit skipping duplicate trade {trade_id} - already processed.")
                        return
            except Exception as e:
                logger.error(f"Error checking bandit_processed_trades for {trade_id}: {e}")

        mu0, n0, alpha0, beta0 = self.get_posterior(context, arm)

        r = float(r_multiple)
        n1 = n0 + 1.0
        mu1 = (n0 * mu0 + r) / n1
        alpha1 = alpha0 + 0.5
        beta1 = beta0 + (n0 * (r - mu0) ** 2) / (2.0 * n1)

        self.save_posterior(context, arm, mu1, n1, alpha1, beta1, r_multiple=r, trade_id=trade_id, strategy=strategy)
        logger.info(
            f"🎰 Bandit Posterior Updated for {context} {self.arm_to_id(arm)}: "
            f"R={r:+.2f} -> mu: {mu0:.3f}->{mu1:.3f}, n: {n0:.0f}->{n1:.0f} (trade_id: {trade_id})"
        )

    def get_state_dict(self) -> Dict[str, Any]:
        """Returns comprehensive state for API inspection and dashboard display."""
        posteriors_by_context: Dict[str, List[Dict[str, Any]]] = {ctx: [] for ctx in CONTEXTS}

        try:
            with self._get_conn() as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    SELECT context, arm_id, strategy_id, risk_level, mu, n, alpha, beta,
                           total_trades, rolling_r_json, quarantined_until, updated_at
                    FROM bandit_posteriors
                """)
                for r in cursor.fetchall():
                    ctx = r["context"]
                    if ctx in posteriors_by_context:
                        is_q = False
                        if r["quarantined_until"]:
                            try:
                                is_q = datetime.fromisoformat(r["quarantined_until"]) > datetime.now(timezone.utc)
                            except Exception:
                                pass
                        trades = r["total_trades"]
                        is_proven = trades >= 30
                        sample_status_text = (
                            f"İstatistiki olarak anlamlı örneklem (N={trades})"
                            if is_proven
                            else f"Veri toplanıyor, henüz yeterli örneklem yok (N={trades}/30)"
                        )
                        posteriors_by_context[ctx].append({
                            "arm_id": r["arm_id"],
                            "strategy_id": r["strategy_id"],
                            "risk_level": r["risk_level"],
                            "mu": round(r["mu"], 4),
                            "n": round(r["n"], 1),
                            "alpha": round(r["alpha"], 2),
                            "beta": round(r["beta"], 2),
                            "total_trades": trades,
                            "is_proven": is_proven,
                            "sample_status": "statistically_significant" if is_proven else "insufficient_sample",
                            "sample_status_text": sample_status_text,
                            "is_quarantined": is_q,
                            "quarantined_until": r["quarantined_until"],
                            "updated_at": r["updated_at"],
                        })
        except Exception as e:
            logger.error(f"Failed to fetch bandit state: {e}")

        # Find currently preferred arm per context with N>=30 guard
        preferred_by_context: Dict[str, Any] = {}
        for ctx, arms_list in posteriors_by_context.items():
            if arms_list:
                active_arms = [a for a in arms_list if not a["is_quarantined"]]
                if active_arms:
                    best = max(active_arms, key=lambda x: x["mu"])
                    best_trades = best.get("total_trades", 0)
                    best_proven = best_trades >= 30
                    best["claim_status"] = (
                        f"Kanıtlanmış Performans (N={best_trades})"
                        if best_proven
                        else f"Veri toplanıyor, henüz yeterli örneklem yok (N={best_trades}/30)"
                    )
                    best["is_proven"] = best_proven
                    preferred_by_context[ctx] = best
                else:
                    preferred_by_context[ctx] = arms_list[0]
            else:
                preferred_by_context[ctx] = {
                    "strategy_id": "momentum_dip_rebound",
                    "risk_level": 1,
                    "mu": 0.0,
                    "total_trades": 0,
                    "is_proven": False,
                    "claim_status": "Veri toplanıyor, henüz yeterli örneklem yok (N=0/30)",
                }

        return {
            "success": True,
            "total_arms_defined": len(ARMS),
            "total_contexts_defined": len(CONTEXTS),
            "contexts": CONTEXTS,
            "arms": [{"strategy_id": s, "risk_level": l, "arm_id": f"{s}__L{l}"} for s, l in ARMS],
            "preferred_by_context": preferred_by_context,
            "posteriors": posteriors_by_context,
        }


# Authoritative Global Singleton
contextual_bandit = ContextualBandit()
