"""
Adaptive Learning & Strategy Parameter Tuning Engine
====================================================
Transforms post-trade reflections into concrete mathematical adjustments:
1. Dynamic Symbol Cooldowns & Penalty:
   - Temporarily silences symbols suffering adverse momentum or consecutive stop-losses.
2. Dynamic ATR Multiplier Calibration:
   - Widens stop buffer if trades are stopped out prematurely by normal market noise.
3. Dynamic Opportunity Score Gate:
   - Raises bar during low-expectancy regimes to enforce extreme selectivity.
4. Dynamic RSI Dip-Buying Threshold:
   - Tightens entry cutoff if dip-buying entries occurred during falling knives.
5. SQLite Persistence:
   - Retains learned rules and parameters across restarts.
"""

import asyncio
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from services.analytics.trade_reflection import (
    TradeReflectionRecord,
    trade_reflection_engine,
)
from shared.enums import SignalDirection
from shared.logging import add_system_log, get_logger

logger = get_logger("adaptive-learning", service="strategy_engine")


class AdaptiveLearningEngine:
    """
    Autonomous Post-Trade Learner and Parameter Adapter.
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path
        self.symbol_metrics: Dict[str, Dict[str, Any]] = {}
        self.dynamic_atr_multiplier: float = 1.20
        self.dynamic_min_opportunity_score: float = 50.0
        self.dynamic_rsi_dip_cutoff: float = 32.0
        self.recent_trade_outcomes: List[bool] = []  # True = win, False = loss (rolling 20)
        self.total_trades_analyzed: int = 0
        self.total_wins: int = 0
        self.total_losses: int = 0
        self.ledger_closed_trades: int = 0
        self.ledger_wins: int = 0
        self.ledger_losses: int = 0
        self.historical_prior_trades: int = 0

        if self.db_path:
            self._init_db()
            self._restore_from_db()

    def set_db_path(self, db_path: str):
        self.db_path = db_path
        self._init_db()
        self._restore_from_db()

    def _get_conn(self) -> Optional[sqlite3.Connection]:
        if not self.db_path:
            return None
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        conn = self._get_conn()
        if not conn:
            return
        try:
            with conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS adaptive_learning_state (
                        id INTEGER PRIMARY KEY,
                        dynamic_atr_multiplier REAL NOT NULL,
                        dynamic_min_opportunity_score REAL NOT NULL,
                        dynamic_rsi_dip_cutoff REAL NOT NULL,
                        total_trades_analyzed INTEGER NOT NULL,
                        total_wins INTEGER NOT NULL,
                        total_losses INTEGER NOT NULL,
                        symbol_metrics_json TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS adaptive_processed_positions (
                        position_id TEXT PRIMARY KEY,
                        symbol TEXT NOT NULL,
                        realized_pnl REAL NOT NULL,
                        processed_at TEXT NOT NULL
                    );
                """)
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS adaptive_parameter_history (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        parameter_name TEXT NOT NULL,
                        old_value REAL NOT NULL,
                        new_value REAL NOT NULL,
                        reason TEXT NOT NULL,
                        sample_size INTEGER NOT NULL,
                        created_at TEXT NOT NULL
                    );
                """)
        except Exception as e:
            logger.error(f"Failed to initialize adaptive_learning_state table: {e}")
        finally:
            conn.close()

    def _record_parameter_change(self, param_name: str, old_val: float, new_val: float, reason: str, sample_size: int):
        conn = self._get_conn()
        if not conn:
            return
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            with conn:
                conn.execute("""
                    INSERT INTO adaptive_parameter_history (parameter_name, old_value, new_value, reason, sample_size, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (param_name, old_val, new_val, reason, sample_size, now_iso))
        except Exception as e:
            logger.error(f"Failed to record parameter change: {e}")
        finally:
            conn.close()

    def _persist_state(self):
        conn = self._get_conn()
        if not conn:
            return
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            metrics_json = json.dumps(self.symbol_metrics)
            with conn:
                conn.execute("""
                    INSERT INTO adaptive_learning_state (
                        id, dynamic_atr_multiplier, dynamic_min_opportunity_score,
                        dynamic_rsi_dip_cutoff, total_trades_analyzed, total_wins,
                        total_losses, symbol_metrics_json, updated_at
                    )
                    VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        dynamic_atr_multiplier=excluded.dynamic_atr_multiplier,
                        dynamic_min_opportunity_score=excluded.dynamic_min_opportunity_score,
                        dynamic_rsi_dip_cutoff=excluded.dynamic_rsi_dip_cutoff,
                        total_trades_analyzed=excluded.total_trades_analyzed,
                        total_wins=excluded.total_wins,
                        total_losses=excluded.total_losses,
                        symbol_metrics_json=excluded.symbol_metrics_json,
                        updated_at=excluded.updated_at
                """, (
                    self.dynamic_atr_multiplier,
                    self.dynamic_min_opportunity_score,
                    self.dynamic_rsi_dip_cutoff,
                    self.total_trades_analyzed,
                    self.total_wins,
                    self.total_losses,
                    metrics_json,
                    now_iso,
                ))
        except Exception as e:
            logger.error(f"Failed to persist adaptive learning state: {e}")
        finally:
            conn.close()

    def _restore_from_db(self):
        conn = self._get_conn()
        if not conn:
            return
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM adaptive_learning_state WHERE id=1")
            row = cur.fetchone()
            if row:
                self.dynamic_atr_multiplier = float(row["dynamic_atr_multiplier"])
                self.dynamic_min_opportunity_score = float(row["dynamic_min_opportunity_score"])
                self.dynamic_rsi_dip_cutoff = float(row["dynamic_rsi_dip_cutoff"])
                self.total_trades_analyzed = int(row["total_trades_analyzed"])
                self.total_wins = int(row["total_wins"])
                self.total_losses = int(row["total_losses"])
                try:
                    self.symbol_metrics = json.loads(row["symbol_metrics_json"])
                except Exception:
                    self.symbol_metrics = {}
                logger.info(
                    f"Restored Adaptive Learning State: ATR_mult={self.dynamic_atr_multiplier:.2f}, "
                    f"MinOpp={self.dynamic_min_opportunity_score:.1f}, "
                    f"AnalyzedTrades={self.total_trades_analyzed} (Wins: {self.total_wins}, Losses: {self.total_losses})"
                )
        except Exception as e:
            logger.error(f"Failed to restore adaptive learning state: {e}")
        finally:
            conn.close()

    def is_symbol_in_cooldown(self, symbol: str) -> Tuple[bool, str, float]:
        """
        Returns (is_cooldown, reason, remaining_seconds).
        """
        metrics = self.symbol_metrics.get(symbol)
        if not metrics:
            return False, "", 0.0

        cooldown_until = metrics.get("cooldown_until", 0.0)
        now_ts = datetime.now(timezone.utc).timestamp()
        if now_ts < cooldown_until:
            rem = round(cooldown_until - now_ts, 1)
            reason = metrics.get("last_lesson", "Son işlemdeki zarar nedeniyle soğuma sürecinde.")
            return True, reason, rem

        return False, "", 0.0

    def get_adapted_confidence(self, symbol: str, base_confidence: float) -> float:
        """
        Adjusts raw signal confidence based on symbol's recent track record.
        """
        metrics = self.symbol_metrics.get(symbol, {})
        consec_losses = metrics.get("consecutive_losses", 0)
        penalty = min(0.20, consec_losses * 0.06)
        return max(0.50, round(base_confidence - penalty, 4))

    def get_min_opportunity_score(self) -> float:
        """Returns dynamically calibrated minimum opportunity score."""
        return self.dynamic_min_opportunity_score

    def get_adapted_atr_multiplier(self) -> float:
        """Returns dynamically calibrated ATR stop multiplier."""
        return self.dynamic_atr_multiplier

    def get_adapted_rsi_cutoff(self) -> float:
        """Returns dynamically calibrated RSI dip entry cutoff."""
        return self.dynamic_rsi_dip_cutoff

    def record_closed_trade(
        self,
        position: Any,
        exit_reason: str,
        db_path: Optional[str] = None,
    ) -> Optional[TradeReflectionRecord]:
        """
        Synchronous/Async hybrid processor called immediately upon position close.
        Analyzes the trade, logs lessons, updates dynamic parameters, and persists state.
        """
        if db_path and not self.db_path:
            self.set_db_path(db_path)

        symbol = getattr(position, "symbol", "UNKNOWN")
        entry_price = float(getattr(position, "entry_price", 0.0))
        exit_price = float(getattr(position, "current_price", entry_price))
        realized_pnl = float(getattr(position, "realized_pnl", 0.0))
        qty = float(getattr(position, "quantity", 0.0))
        notional = entry_price * qty if (entry_price > 0 and qty > 0) else 1.0
        pnl_pct = (realized_pnl / notional) * 100.0 if notional > 0 else 0.0

        opened_at = getattr(position, "opened_at", None)
        closed_at = getattr(position, "closed_at", None) or datetime.now(timezone.utc)
        duration_minutes = 0.0
        if opened_at:
            try:
                diff = (closed_at - opened_at).total_seconds()
                duration_minutes = max(0.0, diff / 60.0)
            except Exception:
                pass

        side_val = getattr(position, "side", None)
        side_str = side_val.value if (side_val is not None and hasattr(side_val, "value")) else str(side_val or "LONG")
        direction = SignalDirection.LONG if "LONG" in side_str.upper() else SignalDirection.SHORT

        fees_paid = float(getattr(position, "fees_paid", 0.0))
        slippage_cost = 0.0
        peak_price = getattr(position, "peak_price", None)

        # Idempotency check: avoid double learning for already processed positions
        now_ts = datetime.now(timezone.utc).timestamp()
        trade_id = str(getattr(position, "position_id", getattr(position, "id", f"pos_{int(now_ts)}")))
        conn_check = self._get_conn()
        if conn_check:
            try:
                cur = conn_check.cursor()
                cur.execute("SELECT 1 FROM adaptive_processed_positions WHERE position_id = ?", (trade_id,))
                if cur.fetchone():
                    logger.info(f"Position {trade_id} already processed in adaptive learning. Skipping.")
                    return None
            except Exception as e:
                logger.warning(f"Error checking adaptive_processed_positions: {e}")
            finally:
                conn_check.close()

        is_win = realized_pnl > 0
        self.total_trades_analyzed += 1
        if is_win:
            self.total_wins += 1
            self.recent_trade_outcomes.append(True)
        else:
            self.total_losses += 1
            self.recent_trade_outcomes.append(False)

        if len(self.recent_trade_outcomes) > 20:
            self.recent_trade_outcomes.pop(0)

        # Mark as processed in adaptive_processed_positions
        conn_proc = self._get_conn()
        if conn_proc:
            try:
                with conn_proc:
                    conn_proc.execute("""
                        INSERT OR IGNORE INTO adaptive_processed_positions (position_id, symbol, realized_pnl, processed_at)
                        VALUES (?, ?, ?, ?)
                    """, (trade_id, symbol, realized_pnl, datetime.now(timezone.utc).isoformat()))
            except Exception as e:
                logger.warning(f"Failed to record processed position {trade_id}: {e}")
            finally:
                conn_proc.close()

        # 1. Update Symbol-Specific Metrics
        if symbol not in self.symbol_metrics:
            self.symbol_metrics[symbol] = {
                "wins": 0,
                "losses": 0,
                "consecutive_losses": 0,
                "cooldown_until": 0.0,
                "last_exit_reason": "",
                "last_lesson": "",
            }

        sym_data = self.symbol_metrics[symbol]
        sym_data["last_exit_reason"] = exit_reason

        # 2. Trigger Trade Reflection & Diagnostic Rules
        reflection: Optional[TradeReflectionRecord] = None

        try:
            try:
                loop = asyncio.get_running_loop()
                coro = trade_reflection_engine.reflect_on_closed_trade(
                    trade_id=trade_id,
                    symbol=symbol,
                    direction=direction,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    realized_pnl=realized_pnl,
                    pnl_pct=pnl_pct,
                    exit_reason=exit_reason,
                    duration_minutes=duration_minutes,
                    peak_price=peak_price,
                    fees_paid=fees_paid,
                    slippage_cost=slippage_cost,
                )
                loop.create_task(coro)
            except RuntimeError:
                diag = trade_reflection_engine._diagnose_trade_root_cause(
                    is_win=is_win,
                    exit_reason=exit_reason,
                    realized_pnl=realized_pnl,
                    pnl_pct=pnl_pct,
                    duration_minutes=duration_minutes,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    peak_price=peak_price,
                    fees_paid=fees_paid,
                    slippage_cost=slippage_cost,
                )
                reflection = TradeReflectionRecord(
                    reflection_id=f"refl-{trade_id}-{int(now_ts)}",
                    symbol=symbol,
                    direction=direction,
                    entry_price=entry_price,
                    exit_price=exit_price,
                    realized_pnl=realized_pnl,
                    pnl_pct=pnl_pct,
                    exit_reason=exit_reason,
                    duration_minutes=duration_minutes,
                    lesson_learned=diag["lesson_learned"],
                    root_cause=diag["root_cause"],
                    recommendation=diag["recommendation"],
                    adaptive_adjustment=diag["adjustment"],
                    fees_paid=fees_paid,
                    slippage_cost=slippage_cost,
                    peak_price=peak_price,
                    category=diag["category"],
                )
                trade_reflection_engine.memory.append(reflection)
                trade_reflection_engine._persist_record(reflection)
        except Exception as e:
            logger.error(f"Error executing trade reflection on {symbol}: {e}")

        # 3. Apply Adaptive Parameter Updates & Statistical Calibration
        adj_summary = []
        if is_win:
            sym_data["wins"] += 1
            sym_data["consecutive_losses"] = 0
            sym_data["cooldown_until"] = 0.0
            sym_data["last_lesson"] = "Başarılı hedef gerçekleşti."
            adj_summary.append("Kazanma serisi (Soğuma kaldırıldı)")

            # Mean Reversion: rollback adapted parameters towards baseline on wins
            if self.dynamic_atr_multiplier > 1.20:
                old_atr = self.dynamic_atr_multiplier
                self.dynamic_atr_multiplier = max(1.20, round(self.dynamic_atr_multiplier - 0.05, 2))
                self._record_parameter_change("dynamic_atr_multiplier", old_atr, self.dynamic_atr_multiplier, f"Mean reversion towards baseline 1.20 on win {symbol}", 1)
                adj_summary.append(f"ATR Stop Çarpanı {self.dynamic_atr_multiplier:.2f}'ye normalleştirildi")

            if self.dynamic_rsi_dip_cutoff < 30.0:
                old_rsi = self.dynamic_rsi_dip_cutoff
                self.dynamic_rsi_dip_cutoff = min(30.0, round(self.dynamic_rsi_dip_cutoff + 0.5, 1))
                self._record_parameter_change("dynamic_rsi_dip_cutoff", old_rsi, self.dynamic_rsi_dip_cutoff, f"Mean reversion towards baseline 30.0 on win {symbol}", 1)
                adj_summary.append(f"RSI Dip Tavanı {self.dynamic_rsi_dip_cutoff:.1f}'e normalleştirildi")
        else:
            sym_data["losses"] += 1
            sym_data["consecutive_losses"] += 1

            cooldown_secs = 900 if sym_data["consecutive_losses"] == 1 else 1800
            sym_data["cooldown_until"] = now_ts + cooldown_secs
            lesson_txt = f"{symbol} stoplandı ({exit_reason}). {cooldown_secs // 60} dk soğuma uygulandı."
            sym_data["last_lesson"] = lesson_txt
            adj_summary.append(f"{symbol} {cooldown_secs // 60} dk soğumaya alındı")

            # Premature Stop Needle Protection (Bounded and Logged to Parameter History)
            if peak_price and entry_price > 0 and ((peak_price - entry_price) / entry_price) >= 0.005:
                old_atr = self.dynamic_atr_multiplier
                self.dynamic_atr_multiplier = min(1.80, round(self.dynamic_atr_multiplier + 0.10, 2))
                self._record_parameter_change("dynamic_atr_multiplier", old_atr, self.dynamic_atr_multiplier, f"Premature stop on {symbol} (peak +{((peak_price - entry_price) / entry_price):.1%})", 1)
                adj_summary.append(f"ATR Stop Çarpanı {self.dynamic_atr_multiplier:.2f}'ye genişletildi (İğne koruması)")

            # Deep Dip Protection for Fast Stop-Outs (< 15 mins)
            if duration_minutes <= 15.0:
                old_rsi = self.dynamic_rsi_dip_cutoff
                self.dynamic_rsi_dip_cutoff = max(26.0, round(self.dynamic_rsi_dip_cutoff - 1.0, 1))
                self._record_parameter_change("dynamic_rsi_dip_cutoff", old_rsi, self.dynamic_rsi_dip_cutoff, f"Fast stop-out on {symbol} within {duration_minutes:.1f}m", 1)
                adj_summary.append(f"RSI Dip Tavanı {self.dynamic_rsi_dip_cutoff:.1f}'e çekildi (Daha derin dip aranıyor)")

        # 4. Global Expectancy & Win-Rate Gate Calibration
        if len(self.recent_trade_outcomes) >= 3:
            recent_wr = sum(1 for w in self.recent_trade_outcomes if w) / len(self.recent_trade_outcomes)
            sample_size = len(self.recent_trade_outcomes)
            if recent_wr < 0.40:
                if self.dynamic_min_opportunity_score < 60.0:
                    old_score = self.dynamic_min_opportunity_score
                    self.dynamic_min_opportunity_score = 60.0
                    self._record_parameter_change("dynamic_min_opportunity_score", old_score, 60.0, f"Recent win rate below 40% ({recent_wr:.1%})", sample_size)
                    adj_summary.append("Zarar serisi nedeniyle Asgari Fırsat Skoru 50 -> 60'a yükseltildi (Aşırı Seçici Mod)")
            elif recent_wr >= 0.55:
                if self.dynamic_min_opportunity_score > 50.0:
                    old_score = self.dynamic_min_opportunity_score
                    self.dynamic_min_opportunity_score = 50.0
                    self._record_parameter_change("dynamic_min_opportunity_score", old_score, 50.0, f"Recent win rate recovered to {recent_wr:.1%}", sample_size)
                    adj_summary.append("Kazanma oranı toparlandı: Asgari Fırsat Skoru 50.0'a geri çekildi")

        # 5. Persist State to SQLite
        self._persist_state()

        # 6. Broadcast System Log
        outcome_str = "KÂR" if is_win else "ZARAR"
        lesson_msg = sym_data["last_lesson"]
        adj_str = ", ".join(adj_summary) if adj_summary else "Parametreler korundu"
        log_level = "SUCCESS" if is_win else "WARNING"

        full_log = f"🧠 [ÖZ-ÖĞRENME] {symbol} {outcome_str} (${realized_pnl:+.2f}): {lesson_msg} | UYARLAMA: {adj_str}"
        add_system_log(full_log, level=log_level, service="strategy")
        logger.info(full_log)

        return reflection

    def reconcile_with_paper_positions(self, db_path: Optional[str] = None) -> int:
        """
        Reconciles adaptive learning state with authoritative paper_positions ledger.
        Processes only closed trades that haven't been processed yet into adaptive_processed_positions.
        """
        if db_path and not self.db_path:
            self.set_db_path(db_path)
        conn = self._get_conn()
        if not conn:
            return 0

        unprocessed = []
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT p.position_id, p.symbol, p.side, p.entry_price, p.current_price,
                       p.realized_pnl, p.quantity, p.opened_at, p.closed_at,
                       p.fees_paid, p.peak_price
                FROM paper_positions p
                LEFT JOIN adaptive_processed_positions a ON p.position_id = a.position_id
                WHERE p.status = 'CLOSED' AND a.position_id IS NULL
                ORDER BY p.closed_at ASC
            """)
            rows = cur.fetchall()
            for r in rows:
                pnl = r[5] or 0.0
                inferred_reason = "TAKE_PROFIT" if pnl > 0 else "STOP_LOSS"
                unprocessed.append({
                    "position_id": r[0],
                    "symbol": r[1],
                    "side": r[2],
                    "entry_price": r[3],
                    "current_price": r[4],
                    "realized_pnl": pnl,
                    "quantity": r[6],
                    "opened_at": r[7],
                    "closed_at": r[8],
                    "exit_reason": inferred_reason,
                    "fees_paid": r[9] or 0.0,
                    "peak_price": r[10],
                })
        except Exception as e:
            logger.error(f"Error querying unprocessed paper positions: {e}")
            return 0
        finally:
            conn.close()

        count = 0
        for item in unprocessed:
            class DummyPos:
                pass
            pos = DummyPos()
            for k, v in item.items():
                setattr(pos, k, v)
            self.record_closed_trade(pos, exit_reason=item["exit_reason"] or "CLOSED")
            count += 1

        if count > 0:
            logger.info(f"Reconciled {count} closed positions with adaptive learning engine.")
        return count

    def retrospective_learn_from_history(
        self,
        closed_positions: List[Any],
        db_path: Optional[str] = None,
    ) -> int:
        """
        Scans closed positions history to backfill reflections and initialize learned state.
        """
        if not closed_positions:
            return 0

        analyzed_count = 0
        for pos in closed_positions:
            res = self.record_closed_trade(pos, exit_reason=getattr(pos, "exit_reason", None) or "HISTORICAL_CLOSE", db_path=db_path)
            if res is not None:
                analyzed_count += 1

        if analyzed_count > 0:
            logger.info(f"Retrospective learning completed for {analyzed_count} historical trades.")
        return analyzed_count

    def get_summary(self) -> Dict[str, Any]:
        """Returns comprehensive adaptive metrics for API and monitoring."""
        now_ts = datetime.now(timezone.utc).timestamp()
        active_cooldowns = {}
        for sym, data in self.symbol_metrics.items():
            cd_until = data.get("cooldown_until", 0.0)
            if cd_until > now_ts:
                active_cooldowns[sym] = {
                    "remaining_seconds": round(cd_until - now_ts, 1),
                    "consecutive_losses": data.get("consecutive_losses", 0),
                    "last_lesson": data.get("last_lesson", ""),
                }

        recent_wr = (
            round((sum(1 for w in self.recent_trade_outcomes if w) / len(self.recent_trade_outcomes)) * 100.0, 1)
            if self.recent_trade_outcomes
            else 0.0
        )

        ledger_count = 0
        conn = self._get_conn()
        if conn:
            try:
                cur = conn.cursor()
                cur.execute("SELECT COUNT(*) FROM adaptive_processed_positions")
                row = cur.fetchone()
                if row:
                    ledger_count = row[0]
            except Exception:
                pass
            finally:
                conn.close()

        return {
            "total_trades_analyzed": self.total_trades_analyzed,
            "ledger_verified_trades": ledger_count,
            "inherited_historical_trades": max(0, self.total_trades_analyzed - ledger_count),
            "total_wins": self.total_wins,
            "total_losses": self.total_losses,
            "rolling_win_rate_pct": recent_wr,
            "dynamic_parameters": {
                "dynamic_atr_multiplier": self.dynamic_atr_multiplier,
                "dynamic_min_opportunity_score": self.dynamic_min_opportunity_score,
                "dynamic_rsi_dip_cutoff": self.dynamic_rsi_dip_cutoff,
            },
            "active_cooldowns": active_cooldowns,
            "symbol_metrics": self.symbol_metrics,
        }


PROJECT_ROOT = Path(__file__).resolve().parents[2]
adaptive_learning_engine = AdaptiveLearningEngine(
    db_path=str(PROJECT_ROOT / "kripto_agent.db")
)
