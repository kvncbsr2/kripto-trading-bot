"""
Post-Trade Reflection & Episodic Memory Engine.
Inspired by Xtra-Computing/CryptoTrade.

Analyzes closed trades to extract causal reasons for success or failure.
Maintains a rolling episodic memory buffer to prevent repeating recent mistakes.
"""

import json
import sqlite3
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

from pydantic import BaseModel, Field

from core.llm.llm_provider import BaseLLMProvider, get_llm_provider
from shared.enums import SignalDirection
from shared.logging import get_logger

logger = get_logger("trade-reflection", service="analytics")


class TradeReflectionRecord(BaseModel):
    reflection_id: str
    symbol: str
    direction: SignalDirection
    entry_price: float
    exit_price: float
    realized_pnl: float
    pnl_pct: float
    exit_reason: str
    duration_minutes: float
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    lesson_learned: str
    root_cause: str
    recommendation: str
    adaptive_adjustment: Dict[str, Any] = Field(default_factory=dict)
    fees_paid: float = 0.0
    slippage_cost: float = 0.0
    peak_price: Optional[float] = None
    category: str = "GENERAL"


class TradeReflectionEngine:
    """
    Episodic Trade Memory and Post-Trade Reflection Evaluator with SQLite persistence.
    """

    def __init__(
        self,
        max_memory_size: int = 100,
        llm_provider: Optional[BaseLLMProvider] = None,
        db_path: Optional[str] = None,
    ):
        self.memory: Deque[TradeReflectionRecord] = deque(maxlen=max_memory_size)
        self.llm = llm_provider or get_llm_provider()
        self.db_path = db_path
        if self.db_path:
            self._init_db()
            self._restore_from_db()

    def set_db_path(self, db_path: str):
        """Sets or updates the database path and initializes schema."""
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
                    CREATE TABLE IF NOT EXISTS trade_reflections (
                        reflection_id TEXT PRIMARY KEY,
                        symbol TEXT NOT NULL,
                        direction TEXT NOT NULL,
                        entry_price REAL NOT NULL,
                        exit_price REAL NOT NULL,
                        realized_pnl REAL NOT NULL,
                        pnl_pct REAL NOT NULL,
                        exit_reason TEXT NOT NULL,
                        duration_minutes REAL NOT NULL,
                        timestamp TEXT NOT NULL,
                        lesson_learned TEXT NOT NULL,
                        root_cause TEXT NOT NULL,
                        recommendation TEXT NOT NULL,
                        adaptive_adjustment TEXT,
                        fees_paid REAL DEFAULT 0.0,
                        slippage_cost REAL DEFAULT 0.0,
                        peak_price REAL,
                        category TEXT DEFAULT 'GENERAL'
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS ix_trade_reflections_symbol ON trade_reflections(symbol)")
                conn.execute("CREATE INDEX IF NOT EXISTS ix_trade_reflections_timestamp ON trade_reflections(timestamp)")
        except Exception as e:
            logger.error(f"Failed to initialize trade_reflections table: {e}")
        finally:
            conn.close()

    def _persist_record(self, record: TradeReflectionRecord):
        conn = self._get_conn()
        if not conn:
            return
        try:
            ts_iso = record.timestamp.isoformat() if hasattr(record.timestamp, "isoformat") else str(record.timestamp)
            dir_str = record.direction.value if hasattr(record.direction, "value") else str(record.direction)
            adj_json = json.dumps(record.adaptive_adjustment)
            with conn:
                conn.execute("""
                    INSERT OR REPLACE INTO trade_reflections (
                        reflection_id, symbol, direction, entry_price, exit_price,
                        realized_pnl, pnl_pct, exit_reason, duration_minutes, timestamp,
                        lesson_learned, root_cause, recommendation, adaptive_adjustment,
                        fees_paid, slippage_cost, peak_price, category
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    record.reflection_id,
                    record.symbol,
                    dir_str,
                    record.entry_price,
                    record.exit_price,
                    record.realized_pnl,
                    record.pnl_pct,
                    record.exit_reason,
                    record.duration_minutes,
                    ts_iso,
                    record.lesson_learned,
                    record.root_cause,
                    record.recommendation,
                    adj_json,
                    record.fees_paid,
                    record.slippage_cost,
                    record.peak_price,
                    record.category,
                ))
        except Exception as e:
            logger.error(f"Failed to persist trade reflection {record.reflection_id}: {e}")
        finally:
            conn.close()

    def _restore_from_db(self):
        conn = self._get_conn()
        if not conn:
            return
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM trade_reflections ORDER BY timestamp DESC LIMIT 100")
            rows = cur.fetchall()
            loaded = []
            for r in reversed(rows):
                try:
                    dir_val = SignalDirection(r["direction"]) if r["direction"] in SignalDirection._value2member_map_ else SignalDirection.LONG
                except Exception:
                    dir_val = SignalDirection.LONG

                adj = {}
                if r["adaptive_adjustment"]:
                    try:
                        adj = json.loads(r["adaptive_adjustment"])
                    except Exception:
                        pass

                rec = TradeReflectionRecord(
                    reflection_id=r["reflection_id"],
                    symbol=r["symbol"],
                    direction=dir_val,
                    entry_price=float(r["entry_price"]),
                    exit_price=float(r["exit_price"]),
                    realized_pnl=float(r["realized_pnl"]),
                    pnl_pct=float(r["pnl_pct"]),
                    exit_reason=r["exit_reason"],
                    duration_minutes=float(r["duration_minutes"]),
                    timestamp=datetime.fromisoformat(r["timestamp"]),
                    lesson_learned=r["lesson_learned"],
                    root_cause=r["root_cause"],
                    recommendation=r["recommendation"],
                    adaptive_adjustment=adj,
                    fees_paid=float(r["fees_paid"] or 0.0),
                    slippage_cost=float(r["slippage_cost"] or 0.0),
                    peak_price=float(r["peak_price"]) if r["peak_price"] is not None else None,
                    category=r["category"] or "GENERAL",
                )
                loaded.append(rec)
            self.memory.clear()
            self.memory.extend(loaded)
            if loaded:
                logger.info(f"Restored {len(loaded)} trade reflections from SQLite ({self.db_path}).")
        except Exception as e:
            logger.error(f"Failed to restore trade reflections from DB: {e}")
        finally:
            conn.close()

    def _diagnose_trade_root_cause(
        self,
        is_win: bool,
        exit_reason: str,
        realized_pnl: float,
        pnl_pct: float,
        duration_minutes: float,
        entry_price: float,
        exit_price: float,
        peak_price: Optional[float],
        fees_paid: float,
        slippage_cost: float,
    ) -> Dict[str, Any]:
        """
        Deterministic, causal post-mortem rule engine.
        Produces root cause classification, lesson, recommendation, and adaptive adjustment.
        """
        gross_pnl = realized_pnl + fees_paid

        # 1. Profitable Scenarios
        if is_win:
            if "PARTIAL_TP" in exit_reason or "1R" in exit_reason:
                return {
                    "category": "WIN_PARTIAL_PROFIT",
                    "root_cause": "1R kısmi kâr hedefi başarıyla alındı ve stop seviyesi başabaşa çekilerek risk sıfırlandı.",
                    "lesson_learned": "Hızlı kâr realizasyonu sermaye dönüşüm hızını artırır ve sermayeyi korur.",
                    "recommendation": "1R kısmi kâr alma disiplinini sürdür.",
                    "adjustment": {"confidence_boost": 0.05, "cooldown_seconds": 0},
                }
            elif "TRAILING" in exit_reason or "LOCK" in exit_reason:
                return {
                    "category": "WIN_TRAILING_LOCK",
                    "root_cause": "Tepe kârın %50'si koruma altına alındı ve fiyat geri çekilmesinde kârla kapatıldı.",
                    "lesson_learned": "Kârın tepe noktadan geri verilmesi trailing stop lock mekanizması ile engellendi.",
                    "recommendation": "Trailing kâr kilidini trend piyasalarında genişlet, dalgalı piyasada sıkı tut.",
                    "adjustment": {"confidence_boost": 0.03, "cooldown_seconds": 0},
                }
            elif "TAKE_PROFIT" in exit_reason or "TARGET" in exit_reason:
                return {
                    "category": "WIN_CLEAN_TARGET",
                    "root_cause": "Strateji kâr hedefi hedeflenen Risk/Kazanç oranında doğrudan gerçekleşti.",
                    "lesson_learned": "Sinyal kalitesi ve giriş zamanlaması optimum seviyede çalıştı.",
                    "recommendation": "Bu piyasa rejimindeki momentum kırılım modellerini ödüllendir.",
                    "adjustment": {"confidence_boost": 0.05, "cooldown_seconds": 0},
                }
            else:
                return {
                    "category": "WIN_MANUAL_OR_OTHER",
                    "root_cause": f"Pozisyon {exit_reason} ile pozitif PnL (+${realized_pnl:.2f}) ile sonlandırıldı.",
                    "lesson_learned": "Kâr realize edildi, sermaye serbest bırakıldı.",
                    "recommendation": "Sermayeyi sıradaki yüksek kaliteli fırsata yönlendir.",
                    "adjustment": {"confidence_boost": 0.02, "cooldown_seconds": 0},
                }

        # 2. Loss Scenarios
        # Scenario A: Fee & Slippage Drag (Gross trade was ~breakeven, but fees/slippage turned it into loss)
        if gross_pnl >= -1.0 and realized_pnl < 0:
            return {
                "category": "LOSS_FEE_SLIPPAGE_DRAG",
                "root_cause": f"Brüt işlem başabaşa yakındı (${gross_pnl:+.2f}), ancak komisyon (${fees_paid:.2f}) ve kayma net zarara yol açtı.",
                "lesson_learned": "Mikro scalp işlemlerinde asgari kâr hedefi borsa komisyon ve kayma toplamının en az 3 katı olmalıdır.",
                "recommendation": "Asgari kâr eşiğini komisyon eşiğinin üzerine çek ve yüksek spreadli tahtalardan kaçın.",
                "adjustment": {"min_target_pct_adj": +0.003, "cooldown_seconds": 300},
            }

        # Scenario B: Premature Stop Out / Wick Loss (Stopped out, but peak had run favorably before dropping)
        if peak_price and entry_price > 0:
            peak_pct = ((peak_price - entry_price) / entry_price) * 100.0
            if peak_pct >= 0.5:
                return {
                    "category": "LOSS_PREMATURE_STOP_WICK",
                    "root_cause": f"Fiyat girişte +%{peak_pct:.2f} kâra geçtikten sonra ani iğneyle stop seviyesini (${exit_price:.4f}) tetikledi.",
                    "lesson_learned": "Stop seviyesi piyasa gürültüsüne (noise) çok yakın konulmuş veya kâr erken kilitlenmemiş.",
                    "recommendation": "Dinamik ATR stop çarpanını 1.2'den 1.4-1.5'e genişlet veya daha erken başabaşa çek.",
                    "adjustment": {"atr_multiplier_adj": +0.2, "cooldown_seconds": 600},
                }

        # Scenario C: Momentum Exhaustion / Immediate Reversal
        if duration_minutes <= 15.0 and pnl_pct <= -0.8:
            return {
                "category": "LOSS_MOMENTUM_EXHAUSTION",
                "root_cause": f"Pozisyon açıldıktan sadece {duration_minutes:.1f} dakika sonra sert terse döndü (%{pnl_pct:.2f}).",
                "lesson_learned": "Fiyat lokal tepe noktasında/momentum tükenişinde yakalandı; derin düzeltme beklenmeden girildi.",
                "recommendation": "Giriş için RSI aşırı satım eşiğini daha derin dip seviyelerine (RSI <= 30) sınırla.",
                "adjustment": {"rsi_entry_delta": -2.0, "cooldown_seconds": 900, "confidence_penalty": 0.08},
            }

        # Scenario D: Standard Trend Reversal / Volatility Stop
        return {
            "category": "LOSS_STOP_PROTECTION",
            "root_cause": f"Pozisyon {exit_reason} ile ${exit_price:.4f} seviyesinde sermayeyi korumak amacıyla kapatıldı (%{pnl_pct:.2f}).",
            "lesson_learned": "Zarar kes kuralı sermayeyi büyük çöküşlerden koruma görevini yerine getirdi.",
            "recommendation": "Bu sembolde bir süre bekleme süresi (cooldown) uygula, trend netleşmeden tekrar girme.",
            "adjustment": {"cooldown_seconds": 900, "confidence_penalty": 0.05},
        }

    async def reflect_on_closed_trade(
        self,
        trade_id: str,
        symbol: str,
        direction: SignalDirection,
        entry_price: float,
        exit_price: float,
        realized_pnl: float,
        pnl_pct: float,
        exit_reason: str,
        duration_minutes: float = 0.0,
        market_context_at_exit: Optional[Dict[str, Any]] = None,
        peak_price: Optional[float] = None,
        fees_paid: float = 0.0,
        slippage_cost: float = 0.0,
    ) -> TradeReflectionRecord:
        """
        Synthesizes causal analysis on why this trade succeeded or failed.
        Combines deterministic causal diagnostics with optional LLM enrichment and persists to SQLite.
        """
        is_win = realized_pnl > 0

        # 1. Deterministic Causal Baseline
        diag = self._diagnose_trade_root_cause(
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

        root_cause = diag["root_cause"]
        lesson = diag["lesson_learned"]
        rec = diag["recommendation"]
        adaptive_adj = diag["adjustment"]
        category = diag["category"]

        # 2. Optional LLM Enrichment if provider is available
        if self.llm and not getattr(self.llm, "is_fallback", False):
            system_prompt = (
                "You are a Senior Quantitative Portfolio Post-Mortem Specialist. "
                "Analyze the closed trade objectively. Extract the root cause and a concrete lesson. "
                "Output JSON with keys: 'root_cause', 'lesson_learned', 'recommendation'."
            )
            user_prompt = (
                f"Trade Details:\n"
                f"- Symbol: {symbol}\n"
                f"- Direction: {direction.value}\n"
                f"- Entry: {entry_price}, Exit: {exit_price}\n"
                f"- Realized PnL: ${realized_pnl:.2f} ({pnl_pct:+.2f}%)\n"
                f"- Exit Reason: {exit_reason}\n"
                f"- Duration: {duration_minutes:.1f} mins\n"
                f"- Baseline Root Cause: {root_cause}\n"
                f"- Outcome: {'PROFITABLE' if is_win else 'LOSS'}\n"
                "Provide brief, sharp post-mortem lesson."
            )
            try:
                raw_res = await self.llm.generate(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    temperature=0.2,
                    json_mode=True,
                )
                data = json.loads(raw_res)
                if data.get("root_cause"):
                    root_cause = data["root_cause"]
                if data.get("lesson_learned"):
                    lesson = data["lesson_learned"]
                if data.get("recommendation"):
                    rec = data["recommendation"]
            except Exception as e:
                logger.debug(f"LLM reflection fallback to causal rule engine: {e}")

        record = TradeReflectionRecord(
            reflection_id=f"refl-{trade_id}-{int(datetime.now(timezone.utc).timestamp())}",
            symbol=symbol,
            direction=direction,
            entry_price=entry_price,
            exit_price=exit_price,
            realized_pnl=realized_pnl,
            pnl_pct=pnl_pct,
            exit_reason=exit_reason,
            duration_minutes=duration_minutes,
            lesson_learned=lesson,
            root_cause=root_cause,
            recommendation=rec,
            adaptive_adjustment=adaptive_adj,
            fees_paid=fees_paid,
            slippage_cost=slippage_cost,
            peak_price=peak_price,
            category=category,
        )

        self.memory.append(record)
        self._persist_record(record)
        logger.info(f"Recorded trade reflection for {symbol} ({'WIN' if is_win else 'LOSS'}): {lesson}")
        return record

    def get_recent_reflections(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Returns the most recent N reflection records."""
        items = list(self.memory)[-limit:]
        return [item.model_dump() for item in reversed(items)]

    def get_relevant_insights(
        self, symbol: str, direction: SignalDirection
    ) -> Optional[str]:
        """
        Retrieves episodic insights for a specific symbol & direction before opening a new trade.
        """
        matching = [
            r for r in reversed(list(self.memory))
            if r.symbol == symbol and r.direction == direction
        ]
        if not matching:
            return None

        recent = matching[0]
        if recent.realized_pnl < 0:
            return f"CAUTION: Last {direction.value} trade on {symbol} hit {recent.exit_reason}. Lesson: {recent.lesson_learned}"
        return f"PREVIOUS WIN: Last {direction.value} on {symbol} was profitable. Note: {recent.lesson_learned}"


# Authoritative Global Singleton
PROJECT_ROOT = Path(__file__).resolve().parents[2]
trade_reflection_engine = TradeReflectionEngine(
    db_path=str(PROJECT_ROOT / "kripto_agent.db")
)

