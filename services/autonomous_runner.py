import asyncio
import concurrent.futures
import inspect
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

from services.execution.order_manager import order_manager
from services.feature_engine.features import FeatureEngine
from services.market_data.dynamic_screener import dynamic_screener
from services.market_data.market_data_service import MarketDataService
from services.notification_service.telegram_service import telegram_service
from services.risk_engine.btc_regime_shield import btc_regime_shield
from services.risk_engine.risk_engine import RiskEngine
from services.signal_engine.scorer import SignalScorer
from services.strategy_engine.strategies.r10_rsi_divergence import (
    R10RSIDivergenceStrategy,
    create_r10_strategy_from_settings,
)
from services.strategy_engine.strategies.regime_gated_pullback import RegimeGatedPullbackStrategy
from shared.config import get_settings
from shared.enums import MarketRegime, PositionStatus, SignalDirection, TradingWorkerState
from shared.logging import add_system_log, get_logger
from shared.schemas import FeatureVector, MarketRegimeState

logger = get_logger("autonomous-trader", service="autonomous_trader")
settings = get_settings()

_ACTIVE_RUNNER_INSTANCE = None
_LOCK_FILE = Path("kripto_agent_worker.lock")


def _is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        import psutil
        return psutil.pid_exists(pid)
    except Exception:
        pass
    try:
        import sys
        if sys.platform == "win32":
            import ctypes
            kernel32 = ctypes.windll.kernel32
            SYNCHRONIZE = 0x00100000
            proc = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
            if proc != 0:
                kernel32.CloseHandle(proc)
                return True
            return False
        else:
            os.kill(pid, 0)
            return True
    except (OSError, PermissionError):
        return False

WATCH_SYMBOLS = list(getattr(settings, "DEFAULT_SYMBOLS", [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "DOGE/USDT",
    "ADA/USDT", "AVAX/USDT", "LINK/USDT", "SUI/USDT", "NEAR/USDT", "APT/USDT",
    "ARB/USDT", "OP/USDT", "RENDER/USDT", "FET/USDT", "INJ/USDT", "TIA/USDT",
    "DOT/USDT", "MATIC/USDT", "PEPE/USDT", "SHIB/USDT", "LDO/USDT", "STX/USDT", "GALA/USDT"
]))


def _cycle_interval_for_timeframe(timeframe: str) -> int:
    """
    FIX (2026-09, efficiency): previously cycle_interval was hardcoded to 15s
    regardless of R10_TIMEFRAME. On 1h candles that meant re-fetching/re-evaluating
    the same still-open candle ~240 times per hour — not incorrect (the causal
    strategy only emits a signal on a newly CLOSED candle), just wasted API calls
    and log noise. Scans at roughly 1/60th of the candle duration, so a newly
    closed candle is still picked up promptly, bounded to [15s, 120s].
    """
    seconds_per_tf = {
        "1m": 60, "5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400,
    }
    tf_seconds = seconds_per_tf.get(timeframe, 900)
    return max(15, min(120, tf_seconds // 60))


class AutonomousPaperTrader:
    """
    Authoritative Causal Autonomous Paper Trader for KRIPTO AGENT V6.1.
    Strictly adheres to Zero Fake Data Policy:
    1. Real market prices from MarketDataService (Binance REST / WS).
    2. Real technical indicators and causal pivot confirmation (R10RSIDivergenceStrategy).
    3. Mandatory RiskEngine gate (Signals cannot bypass RiskEngine).
    4. Spot mode strictly suppresses SHORT execution (SIGNAL_ONLY).
    5. Starts in OFF state by default (is_active = False).
    """

    def __init__(
        self,
        command_bus_instance=None,
        market_data_service: Optional[MarketDataService] = None,
        risk_engine: Optional[RiskEngine] = None,
    ):
        settings = get_settings()
        self.command_bus = command_bus_instance
        self.market_data_service = market_data_service
        self.risk_engine = risk_engine or RiskEngine(
            is_spot_mode=True,
            max_open_positions=settings.MAX_OPEN_POSITIONS,
            daily_max_loss_usd=settings.DAILY_MAX_LOSS,
            risk_per_trade=settings.RISK_PER_TRADE,
            max_trades_per_day=settings.MAX_TRADES_PER_DAY,
            max_position_equity_ratio=getattr(settings, "MAX_POSITION_EQUITY_RATIO", 0.40),
            min_risk_reward=getattr(settings, "MIN_RISK_REWARD", 1.5),
            target_mode=getattr(settings, "TARGET_MODE", "SOFT"),
            daily_target_min=getattr(settings, "DAILY_TARGET_MIN", 50.0),
            daily_target_max=getattr(settings, "DAILY_TARGET_MAX", 100.0),
        )
        self.strategy = create_r10_strategy_from_settings()
        self.pullback_strategy = None
        self.feature_engine = FeatureEngine()

        # Authoritative State Machine (P0-001)
        self.worker_state: TradingWorkerState = TradingWorkerState.STOPPED
        self.is_active: bool = False
        self.cycle_interval: int = _cycle_interval_for_timeframe(settings.R10_TIMEFRAME)
        self.cycle_count: int = 0
        self.last_cycle_at: Optional[str] = None
        self.last_action: str = "Otonom motor beklemede (STOPPED)."
        self._task: Optional[asyncio.Task] = None
        self._lock_token: Optional[str] = None
        # Closed-candle deduplication & bar-boundary tracking (P0-CANDLE-DEDUP)
        self.last_processed_candle_timestamp: Dict[str, Any] = {}
        self.last_scanned_bucket: Optional[int] = None

    def bind_command_bus(self, command_bus_instance):
        self.command_bus = command_bus_instance
        if hasattr(command_bus_instance, "strategy_manager") and command_bus_instance.strategy_manager:
            r10 = next((s for s in command_bus_instance.strategy_manager.strategies if s.name == "r10_rsi_divergence"), None)
            if r10:
                self.strategy = r10
            # Pullback strategy disabled in standard protocol
            self.pullback_strategy = None

    def bind_market_data_service(self, market_data_service: MarketDataService):
        self.market_data_service = market_data_service

    def _ensure_market_data_service(self) -> MarketDataService:
        if self.market_data_service is None:
            self.market_data_service = MarketDataService(symbols=WATCH_SYMBOLS)
        return self.market_data_service

    def start(self):
        if self.worker_state == TradingWorkerState.RUNNING:
            raise RuntimeError("Duplicate worker start is rejected: Trading worker loop is already RUNNING.")
        if self.worker_state == TradingWorkerState.EMERGENCY_STOP:
            raise RuntimeError("Emergency stop is active: Trading worker cannot start until reset.")

        global _ACTIVE_RUNNER_INSTANCE
        if _ACTIVE_RUNNER_INSTANCE is not None and _ACTIVE_RUNNER_INSTANCE.worker_state == TradingWorkerState.RUNNING and _ACTIVE_RUNNER_INSTANCE is not self:
            raise RuntimeError("Only one trading loop can run at a time: another loop is already active.")

        # Inter-process atomic lock acquisition (P0-001)
        self._lock_token = self._acquire_worker_lock()

        self.worker_state = TradingWorkerState.STARTING
        self.is_active = True
        self.last_action = "Otonom motor aktif. Gerçek piyasa taranıyor."
        self.worker_state = TradingWorkerState.RUNNING
        _ACTIVE_RUNNER_INSTANCE = self
        self._sync_runtime_state()

        try:
            from services.config_manager.state_persistence import update_persisted_state
            update_persisted_state({
                "is_autonomous_active": True,
                "is_halted": False,
                "circuit_suspended": True,
                "circuit_state": "NORMAL",
                "last_action": self.last_action,
            })
        except Exception as e:
            logger.debug(f"Failed to persist active runner state: {e}")

        add_system_log("▶️ OTONOM BOT BAŞLATILDI: Canlı Binance Spot kline/fiyat taraması aktif.", level="SUCCESS", service="runner")
        try:
            asyncio.get_running_loop().create_task(
                telegram_service.notify_bot_started(settings.INITIAL_CAPITAL)
            )
        except RuntimeError:
            logger.debug("Skipped start notification because no event loop is running.")
        if self._task is None or self._task.done():
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                logger.debug("Skipped worker task because no event loop is running.")
            else:
                self._task = loop.create_task(self._run_loop())
                logger.info("Autonomous Paper Trader loop started (RUNNING).")

    def stop(self):
        if self.worker_state == TradingWorkerState.STOPPED:
            return
        self.worker_state = TradingWorkerState.STOPPING
        self.is_active = False
        self.last_action = "Otonom motor durduruldu (STOPPED)."
        self._release_worker_lock(self._lock_token)
        self._lock_token = None
        global _ACTIVE_RUNNER_INSTANCE
        if _ACTIVE_RUNNER_INSTANCE is self:
            _ACTIVE_RUNNER_INSTANCE = None
        self.worker_state = TradingWorkerState.STOPPED
        self._sync_runtime_state()

        try:
            from services.config_manager.state_persistence import update_persisted_state
            update_persisted_state({
                "is_autonomous_active": False,
                "last_action": self.last_action,
            })
        except Exception:
            pass

        add_system_log("⏹️ OTONOM BOT DURDURULDU: Alım-satım taraması duraklatıldı.", level="WARNING", service="runner")
        try:
            asyncio.get_running_loop().create_task(telegram_service.notify_bot_stopped())
        except RuntimeError:
            logger.debug("Skipped stop notification because no event loop is running.")
        if self._task and not self._task.done():
            self._task.cancel()
            self._task = None
            logger.info("Autonomous Paper Trader loop stopping (STOPPED).")

    def pause(self):
        if self.worker_state != TradingWorkerState.RUNNING:
            return
        self.worker_state = TradingWorkerState.PAUSED
        self.is_active = False
        self.last_action = "Otonom motor duraklatıldı (PAUSED)."
        self._sync_runtime_state()
        try:
            from services.config_manager.state_persistence import update_persisted_state
            update_persisted_state({
                "is_autonomous_active": False,
                "last_action": self.last_action,
            })
        except Exception:
            pass
        add_system_log("⏸️ OTONOM BOT DURAKLATILDI (PAUSED).", level="INFO", service="runner")

    def resume(self):
        if self.worker_state == TradingWorkerState.EMERGENCY_STOP:
            raise RuntimeError("Cannot resume from EMERGENCY_STOP. Call reset_state() first.")
        if self.worker_state == TradingWorkerState.RUNNING:
            return
        self.worker_state = TradingWorkerState.RUNNING
        self.is_active = True
        self.last_action = "Otonom motor devam ettirildi (RUNNING)."
        self._sync_runtime_state()
        try:
            from services.config_manager.state_persistence import update_persisted_state
            update_persisted_state({
                "is_autonomous_active": True,
                "is_halted": False,
                "last_action": self.last_action,
            })
        except Exception:
            pass
        add_system_log("▶️ OTONOM BOT DEVAM ETTİRİLDİ (RUNNING).", level="SUCCESS", service="runner")

    def emergency_stop(self, reason: str = "Acil durum emri verildi."):
        self.worker_state = TradingWorkerState.EMERGENCY_STOP
        self.is_active = False
        self.last_action = f"🚨 ACİL DURDURMA: {reason}"
        # KRİTİK INVARIANT: Acil durdurma anında kilit serbest BIRAKILMAZ!
        # Böylece acil durumdayken ikinci bir sürecin/iş parçacığının devreye girip yeni trade açması engellenir.
        # Kilit yalnızca operatörün açık bir reset_state() çağrısıyla serbest bırakılır.
        if self._task and not self._task.done():
            self._task.cancel()
            self._task = None
        if self.command_bus and getattr(self.command_bus, "broker", None):
            res = self.command_bus.broker.emergency_close_all()
            if inspect.isawaitable(res):
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    loop = None
                if loop and loop.is_running():
                    with concurrent.futures.ThreadPoolExecutor() as pool:
                        pool.submit(asyncio.run, res).result()
                else:
                    asyncio.run(res)
        self._sync_runtime_state()
        try:
            from services.config_manager.state_persistence import update_persisted_state
            update_persisted_state({
                "is_autonomous_active": False,
                "is_halted": True,
                "circuit_state": "EMERGENCY",
                "last_action": self.last_action,
            })
        except Exception:
            pass
        add_system_log(f"🚨 ACİL DURDURMA TETİKLENDİ: {reason}", level="ERROR", service="risk")

    def reset_state(self):
        """Allows recovering from EMERGENCY_STOP or ERROR back to clean STOPPED state."""
        self._release_worker_lock(self._lock_token)
        self._lock_token = None
        global _ACTIVE_RUNNER_INSTANCE
        if _ACTIVE_RUNNER_INSTANCE is self:
            _ACTIVE_RUNNER_INSTANCE = None
        self.worker_state = TradingWorkerState.STOPPED
        self.is_active = False
        self.last_action = "Otonom motor sıfırlandı ve hazır (STOPPED)."
        self._sync_runtime_state()
        try:
            from services.config_manager.state_persistence import update_persisted_state
            update_persisted_state({
                "is_halted": False,
                "circuit_state": "NORMAL",
                "last_action": self.last_action,
            })
        except Exception:
            pass

    @classmethod
    def _acquire_worker_lock(cls) -> str:
        """
        Atomically acquires process-level worker lock using OS atomic file creation (O_CREAT | O_EXCL).
        Detects stale locks from crashed processes and recovers safely.
        Returns a unique lock token for ownership verification.
        """
        current_pid = os.getpid()
        token = str(uuid.uuid4())
        payload = json.dumps({
            "pid": current_pid,
            "owner_token": token,
            "timestamp": time.time(),
        })

        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            fd = os.open(str(_LOCK_FILE), flags)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
            return token
        except FileExistsError:
            pass

        # Inspect existing lock
        existing_pid = -1
        try:
            content = _LOCK_FILE.read_text(encoding="utf-8").strip()
            data = json.loads(content)
            existing_pid = int(data.get("pid", -1))
        except Exception:
            try:
                existing_pid = int(content)
            except Exception:
                existing_pid = -1

        if existing_pid == current_pid:
            try:
                _LOCK_FILE.write_text(payload, encoding="utf-8")
            except Exception:
                pass
            return token

        lock_age = 0.0
        try:
            lock_age = time.time() - float(data.get("timestamp", time.time()))
        except Exception:
            pass

        if existing_pid > 0 and _is_pid_alive(existing_pid) and lock_age < 180.0:
            raise RuntimeError(
                f"Only one trading loop can run at a time: another loop is already active in process PID {existing_pid}."
            )

        # Stale lock from deceased process or expired lock -> clean and retry
        logger.warning(f"Removing stale worker lock from deceased or expired PID {existing_pid} (age: {lock_age:.1f}s)")
        try:
            _LOCK_FILE.unlink(missing_ok=True)
        except Exception:
            pass

        try:
            fd = os.open(str(_LOCK_FILE), flags)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(payload)
            return token
        except FileExistsError:
            raise RuntimeError("Only one trading loop can run at a time: lock acquired by another process.")

    @classmethod
    def _release_worker_lock(cls, token: Optional[str] = None):
        if not _LOCK_FILE.exists():
            return
        try:
            content = _LOCK_FILE.read_text(encoding="utf-8").strip()
            data = json.loads(content)
            existing_pid = int(data.get("pid", -1))
            existing_token = data.get("owner_token")
            if existing_pid == os.getpid():
                if token is None or existing_token == token:
                    _LOCK_FILE.unlink(missing_ok=True)
        except Exception:
            try:
                if int(content) == os.getpid():
                    _LOCK_FILE.unlink(missing_ok=True)
            except Exception:
                pass

    def _calculate_sleep_until_next_bar(
        self,
        timeframe: str,
        now_ts: Optional[float] = None,
        buffer_sec: float = 2.0,
    ) -> float:
        """
        Calculates exact wait seconds until the close of the current bar + safety buffer (Tkapanis + buffer_sec).
        Ensures strategy evaluations run strictly right after fresh candle close without drifting.
        Eliminates the artificial 3-8s clamp (P0-001 Scheduler Invariant).
        """
        seconds_map = {
            "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
            "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "8h": 28800,
            "12h": 43200, "1d": 86400, "3d": 259200, "1w": 604800,
        }
        tf_sec = seconds_map.get(str(timeframe).lower(), 900)
        if now_ts is None:
            now_ts = time.time()
        next_close = (int(now_ts // tf_sec) + 1) * tf_sec
        wait_sec = (next_close + buffer_sec) - now_ts
        if wait_sec <= 0:
            wait_sec = buffer_sec
        return wait_sec

    async def _run_loop(self):
        await asyncio.sleep(1)
        # Initialize retrospective learning from any past closed trades
        try:
            broker = self._ensure_broker()
            closed_hist = getattr(broker, "closed_positions_history", [])
            if closed_hist:
                from services.strategy_engine.adaptive_learning import adaptive_learning_engine
                adaptive_learning_engine.retrospective_learn_from_history(
                    closed_hist, db_path=getattr(broker, "db_path", None)
                )
        except Exception as e:
            logger.debug(f"Retrospective learning check skipped on loop init: {e}")

        while True:
            try:
                if self.is_active:
                    await self.step_cycle()
                scan_tf = getattr(self.strategy, "timeframe", None) or settings.R10_TIMEFRAME
                sleep_sec = self._calculate_sleep_until_next_bar(scan_tf)

                broker = getattr(self.command_bus, "broker", None) if self.command_bus else None
                has_open_positions = bool(broker and broker.open_positions)

                # Dynamic responsiveness invariant:
                # If there are open positions, sleep briefly (max 4.0s) so stops/targets are monitored in real-time.
                # Candle-boundary and timestamp deduplication prevent redundant universe scans.
                # When no open positions exist, bound sleep to max 30s to keep heartbeat and process active.
                actual_sleep = min(sleep_sec, 4.0) if has_open_positions else min(sleep_sec, float(self.cycle_interval), 30.0)
                await asyncio.sleep(actual_sleep)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in autonomous trader loop: {e}", exc_info=True)
                await asyncio.sleep(5)

    async def step_cycle(self) -> Dict[str, Any]:
        self.cycle_count += 1
        self.last_cycle_at = datetime.now(timezone.utc).strftime("%H:%M:%S")
        try:
            from services.config_manager.state_persistence import update_persisted_state
            update_persisted_state({
                "last_cycle_at": self.last_cycle_at,
                "last_action": self.last_action,
            })
        except Exception:
            pass

        if not self.command_bus or not getattr(self.command_bus, "broker", None):
            self.last_action = "CommandBus veya Broker hazır değil."
            return {"action": "NO_BROKER", "cycle": self.cycle_count}

        broker = self.command_bus.broker
        runtime_state = getattr(self.command_bus, "runtime_state", {})

        if runtime_state.get("is_halted") or runtime_state.get("system_state") in ["HALTED", "EMERGENCY_SHUTDOWN"]:
            self.last_action = f"Sistem acil durduruldu: {runtime_state.get('system_state')}"
            return {"action": "PAUSED", "cycle": self.cycle_count}

        mds = self._ensure_market_data_service()

        # ---------------------------------------------------------------------
        # 1. Update prices of open positions in PARALLEL with REAL Binance market data
        # ---------------------------------------------------------------------
        open_pos_items = [
            (sym, pos) for sym, pos in list(broker.open_positions.items())
            if pos.status == PositionStatus.OPEN
        ]

        async def _check_single_position(symbol: str, pos: Any) -> Optional[Dict[str, Any]]:
            try:
                ticker = await mds.get_live_ticker(symbol)
                if ticker and "price" in ticker and ticker["price"] > 0:
                    real_price = float(ticker["price"])
                    broker.update_market_price(symbol, real_price)
                    if hasattr(pos, "price_fetch_failures"):
                        pos.price_fetch_failures = 0

                    # Remove from stale_price_symbols in runtime_state if present
                    rt_state = getattr(self.command_bus, "runtime_state", None)
                    if rt_state is not None and "stale_price_symbols" in rt_state:
                        if symbol in rt_state["stale_price_symbols"]:
                            rt_state["stale_price_symbols"].remove(symbol)

                    was_partial = getattr(pos, "partial_tp_hit", False)

                    # Check real SL/TP/trailing
                    result = broker.check_position_stops_and_targets(
                        symbol=symbol,
                        high=real_price,
                        low=real_price,
                        close=real_price,
                    )
                    if result:
                        closed_pos, reason, exit_price = result

                        # Calculate exact R-multiple (AUDIT-R-MULTIPLE)
                        risk_distance = abs(closed_pos.entry_price - closed_pos.stop_loss)
                        risk_amount = risk_distance * closed_pos.quantity if risk_distance > 0 else 1.0
                        r_multiple = closed_pos.realized_pnl / risk_amount if risk_amount > 0 else 0.0
                        closed_pos.r_multiple = round(r_multiple, 3)

                        # Update signals table with trade outcome
                        sig_id = getattr(closed_pos, "signal_id", None)
                        try:
                            from database.repositories.signal_repo import SignalRepository
                            SignalRepository.update_signal_outcome_sync(
                                signal_id=sig_id,
                                realized_pnl=closed_pos.realized_pnl,
                                r_multiple=r_multiple,
                                exit_reason=reason,
                            )
                        except Exception as sig_err:
                            logger.error(f"Failed to record signal outcome for {symbol}: {sig_err}")

                        # Update learning_decisions ledger with finalized trade outcome
                        try:
                            from services.learning.decision_logger import decision_logger
                            decision_logger.finalize_trade_outcome(
                                trade_id=closed_pos.position_id,
                                realized_net_pnl=closed_pos.realized_pnl,
                                realized_r_multiple=r_multiple,
                            )
                        except Exception as ld_err:
                            logger.error(f"Failed to finalize trade outcome in decision_logger: {ld_err}")

                        # Bayesian Contextual Bandit Posterior Update (Item 8: Idempotent & Non-Manual)
                        try:
                            from services.learning.contextual_bandit import contextual_bandit
                            entry_ctx = getattr(closed_pos, "entry_context", None) or "BTC_RANGING_LOW_VOL"
                            entry_arm = getattr(closed_pos, "entry_arm", None)
                            if entry_arm:
                                contextual_bandit.update_posterior(
                                    entry_ctx,
                                    entry_arm,
                                    r_multiple,
                                    trade_id=closed_pos.position_id,
                                    strategy=getattr(closed_pos, "strategy", None),
                                )
                        except Exception as b_err:
                            logger.error(f"Failed to update contextual bandit posterior for {symbol}: {b_err}")

                        # DPO Preference Engine Flywheel Update (Faz 3 & 5)
                        try:
                            from services.learning.preference_engine import PreferenceEngine
                            pe = PreferenceEngine()
                            pe.record_trade_outcome(closed_pos, context=getattr(closed_pos, "entry_context", None) or "UNKNOWN")
                        except Exception as dpo_err:
                            logger.error(f"Failed to ingest trade into DPO preference flywheel for {symbol}: {dpo_err}")

                        action_msg = f"{symbol} {reason} tetiklendi. Kâr/Zarar: ${closed_pos.realized_pnl:+.2f} (R: {r_multiple:+.2f})"
                        logger.info(action_msg)
                        asyncio.create_task(telegram_service.notify_trade_close(
                            pos={"symbol": symbol, "current_price": exit_price, "realized_pnl": closed_pos.realized_pnl, "fees_paid": getattr(closed_pos, "commission_paid", 0.0), "r_multiple": r_multiple},
                            reason=reason
                        ))
                        if self.command_bus and hasattr(self.command_bus, "_log_audit"):
                            self.command_bus._log_audit(
                                action="PAPER_POSITION_CLOSED",
                                parameters={"symbol": symbol, "reason": reason, "exit_price": exit_price, "realized_pnl": closed_pos.realized_pnl, "r_multiple": r_multiple},
                                success=True,
                                result={"balance": broker.balance, "equity": broker.equity},
                            )
                        order_manager.remove_protective_stop(symbol)
                        return {
                            "action": "POSITION_CLOSED",
                            "reason": reason,
                            "symbol": symbol,
                            "pnl": closed_pos.realized_pnl,
                            "r_multiple": r_multiple,
                            "action_msg": action_msg,
                        }
                    elif not was_partial and getattr(pos, "partial_tp_hit", False):
                        partial_pnl = getattr(pos, "partial_realized_pnl", 0.0)
                        action_msg = (
                            f"🎯 {symbol} 1R Kısmi Kâr Alındı (%50). Realize Kâr: +${partial_pnl:.2f}. "
                            f"Kalan pozisyon Stop Loss seviyesi başabaşa (${pos.stop_loss:.2f}) çekildi."
                        )
                        logger.info(action_msg)
                        add_system_log(action_msg, level="INFO", service="execution")
                        asyncio.create_task(telegram_service.notify_trade_close(
                            pos={
                                "symbol": symbol,
                                "current_price": real_price,
                                "realized_pnl": partial_pnl,
                                "fees_paid": getattr(pos, "partial_fees_paid", 0.0),
                            },
                            reason="PARTIAL_TP_1R (50% @ 1R Breakeven Lock)",
                        ))
                        if self.command_bus and hasattr(self.command_bus, "_log_audit"):
                            self.command_bus._log_audit(
                                action="PAPER_POSITION_PARTIAL_EXIT",
                                parameters={
                                    "symbol": symbol,
                                    "reason": "PARTIAL_TP_1R",
                                    "exit_price": real_price,
                                    "partial_pnl": partial_pnl,
                                },
                                success=True,
                                result={"balance": broker.balance, "equity": broker.equity},
                            )
                        return {
                            "action": "POSITION_PARTIAL_EXIT",
                            "symbol": symbol,
                            "pnl": partial_pnl,
                            "remaining_qty": pos.quantity,
                            "action_msg": action_msg,
                        }
                else:
                    raise ValueError(f"Empty or invalid ticker received for {symbol}: {ticker}")
            except Exception as e:
                err_msg = str(e)
                if hasattr(broker, "record_price_fetch_failure"):
                    failures = broker.record_price_fetch_failure(symbol, err_msg)
                else:
                    failures = getattr(pos, "price_fetch_failures", 0) + 1
                    pos.price_fetch_failures = failures
                    if failures >= 5:
                        pos.price_stale = True

                rt_state = getattr(self.command_bus, "runtime_state", None)
                if failures >= 5:
                    logger.critical(
                        f"CRITICAL: {symbol} açık pozisyonu için {failures} ardışık döngüdür canlı fiyat güncellenemiyor! Hata: {err_msg}"
                    )
                    if rt_state is not None:
                        stale_symbols = rt_state.setdefault("stale_price_symbols", [])
                        if symbol not in stale_symbols:
                            stale_symbols.append(symbol)

                    if failures == 5 or failures % 20 == 0:
                        try:
                            warn_msg = f"⚠️ {symbol} için {failures} döngüdür fiyat güncellenemiyor, P&L güvenilmez olabilir. Hata: {err_msg}"
                            asyncio.create_task(telegram_service.send_message(warn_msg))
                        except Exception as tg_err:
                            logger.warning(f"Telegram notification failed (stale price state preserved): {tg_err}")
                else:
                    logger.warning(
                        f"Failed to fetch live price for open position {symbol} (ardışık deneme #{failures}): {err_msg}"
                    )
            return None

        if open_pos_items:
            pos_checks = await asyncio.gather(
                *[_check_single_position(s, p) for s, p in open_pos_items],
                return_exceptions=True
            )
            for res in pos_checks:
                if isinstance(res, dict) and res is not None:
                    if "action_msg" in res:
                        self.last_action = res["action_msg"]
                        del res["action_msg"]
                    self._sync_runtime_state()
                    return res

        # ---------------------------------------------------------------------
        # 1.5 Mathematical Expectancy Circuit Breaker ("Negatife dönerse hemen dur")
        # ---------------------------------------------------------------------
        closed_history = getattr(broker, "closed_positions_history", [])
        exp_tripped, exp_reason, _ = self.risk_engine.circuit_breaker.check_expectancy(closed_history)

        if exp_tripped:
            if self.risk_engine.daily_max_loss_usd < 1000.0:
                halt_msg = f"🛑 {exp_reason}"
                self.last_action = halt_msg
                self.is_active = False
                runtime_state["circuit_state"] = "EXPECTANCY_HALTED"
                runtime_state["is_autonomous_active"] = False
                add_system_log(halt_msg, level="ERROR", service="risk")
                self._sync_runtime_state()
                return {"action": "EXPECTANCY_HALTED", "reason": exp_reason}
            else:
                runtime_state["circuit_state"] = "STRESS_TEST_ACTIVE"
                logger.warning(f"⚠️ EXPECTANCY_ALERT (Stress Test Mode: Daily Max Loss=${self.risk_engine.daily_max_loss_usd}): {exp_reason}")

        # ---------------------------------------------------------------------
        # 1.6 Daily Profit Target Lock Guard ("Her gün kâr gör, kârı geri verme")
        # ---------------------------------------------------------------------
        open_count = len(broker.open_positions)
        is_locked, lock_reason, current_daily_profit = self.risk_engine.is_daily_profit_locked(broker)
        if is_locked:
            lock_msg = f"💰 GÜNLÜK HEDEF KİLİTLENDİ: {lock_reason}. Kâr realize edildi (+${current_daily_profit:.2f}), yeni alımlar durduruldu."
            self.last_action = lock_msg
            logger.info(lock_msg)
            add_system_log(lock_msg, level="INFO", service="risk")
            self._sync_runtime_state()
            return {
                "action": "DAILY_TARGET_LOCKED",
                "reason": lock_reason,
                "daily_realized_pnl": current_daily_profit,
                "open_positions": open_count,
            }

        # ---------------------------------------------------------------------
        # 2. Contextual Bandit Strategy & Risk Profile Adaptation (Bayesian Learner)
        # ---------------------------------------------------------------------
        btc_candles = await mds.get_historical_klines("BTC/USDT", timeframe="15m", limit=30)
        btc_health = btc_regime_shield.evaluate_btc_health(btc_candles)
        if btc_health.is_dumping:
            add_system_log(f"🛡️ BTC TREND KALKANI AKTİF: {btc_health.reason}. Altcoin alımları donduruldu.", level="WARNING", service="risk")

        # Determine Context: {BTC_BULL, BTC_BEAR, BTC_RANGING} x {LOW_VOL, HIGH_VOL}
        current_context = "BTC_RANGING_LOW_VOL"
        if btc_candles and len(btc_candles) >= 5:
            last_btc_p = float(btc_candles[-1].close)
            prev_btc_p = float(btc_candles[-5].close)
            ret_pct = ((last_btc_p - prev_btc_p) / prev_btc_p) * 100.0 if prev_btc_p > 0 else 0.0
            if ret_pct > 0.5:
                trend = "BTC_BULL"
            elif ret_pct < -0.5 or btc_health.is_dumping:
                trend = "BTC_BEAR"
            else:
                trend = "BTC_RANGING"

            highs = [float(c.high) for c in btc_candles[-10:]]
            lows = [float(c.low) for c in btc_candles[-10:]]
            vol_pct = (max(highs) - min(lows)) / last_btc_p if last_btc_p > 0 else 0.0
            vol = "HIGH_VOL" if vol_pct > 0.025 else "LOW_VOL"
            current_context = f"{trend}_{vol}"

        # Contextual Bandit Arm Selection via Bayesian Thompson Sampling
        from services.learning.contextual_bandit import contextual_bandit
        selected_strategy, selected_risk_level = contextual_bandit.select_arm(current_context)
        current_arm_id = f"{selected_strategy}__L{selected_risk_level}"

        # Switch Strategy if different (SYSTEM INVARIANT: Bandit NEVER mutates user risk profile ceiling)
        from apps.api.app.api.state import apply_strategy_to_system
        active_strat = getattr(self.command_bus, "active_strategy_id", None) or getattr(self.strategy, "name", "momentum_dip_rebound")

        should_apply_strat = selected_strategy != active_strat

        if should_apply_strat:
            try:
                apply_strategy_to_system(selected_strategy, source="bandit_contextual")
                arm_desc = f"{selected_strategy}__L{selected_risk_level}"
                add_system_log(
                    f"🎰 BANDIT ADAPTASYONU: Context={current_context} | Seçilen Kol: {arm_desc} "
                    f"(Strateji güncellendi: {selected_strategy}. Risk seviyesi kullanıcı tavanında korundu)",
                    level="INFO",
                    service="learning",
                )
            except Exception as e:
                logger.error(f"Bandit state transition error: {e}")

        # ---------------------------------------------------------------------
        # 2.2 Capacity & Active State Checks for New Positions
        # ---------------------------------------------------------------------
        open_count = len(broker.open_positions)
        override_max = runtime_state.get("max_open_positions_override")
        if override_max is not None:
            max_allowed = int(override_max)
            if hasattr(self, "risk_engine") and self.risk_engine and self.risk_engine.max_open_positions < max_allowed:
                self.risk_engine.max_open_positions = max_allowed
        else:
            max_allowed = int(runtime_state.get("max_open_positions") or self.risk_engine.max_open_positions or 25)

        if open_count >= max_allowed:
            self.last_action = f"Maksimum açık pozisyon doldu ({open_count}/{max_allowed}). Sadece açık pozisyonlar izleniyor."
            self._sync_runtime_state()
            return {"action": "MONITORING_MAX_CAPACITY", "open_positions": open_count}

        # Circuit Breaker Hard Gate: If circuit breaker is tripped, halt universe scanning immediately
        if hasattr(self, "risk_engine") and self.risk_engine:
            cb = getattr(self.risk_engine, "circuit_breaker", None)
            port_state = broker.get_portfolio_state()
            cb_tripped, cb_reason, _ = cb.check(port_state) if cb else (False, None, None)
            if (cb and cb.state.value in ["LOCKED", "EMERGENCY"]) or cb_tripped or runtime_state.get("is_halted"):
                halt_reason = cb_reason or getattr(cb, "trip_reason", None) or "Circuit Breaker Locked"
                self.last_action = f"🛑 Devre kesici kilitli: {halt_reason}. Tarama durduruldu, mevcut {open_count} pozisyon izleniyor."
                runtime_state["circuit_state"] = cb.state.value if cb else "LOCKED"
                runtime_state["is_halted"] = True
                runtime_state["system_state"] = "HALTED"
                self._sync_runtime_state()
                return {"action": "CIRCUIT_BREAKER_LOCKED", "reason": halt_reason, "open_positions": open_count}

        strat_enabled = getattr(self.strategy, "enabled", True)
        if not strat_enabled:
            self.last_action = f"{self.strategy.name} stratejisi devre dışı (RESEARCH_ONLY). Sadece açık pozisyonlar izleniyor."
            add_system_log(
                f"⏸️ {self.strategy.name} Stratejisi devre dışı (RESEARCH_ONLY). Yeni sinyal taranmıyor, mevcut {open_count} pozisyon izleniyor.",
                level="INFO",
                service="runner",
            )
            self._sync_runtime_state()
            return {"action": "STRATEGY_DISABLED", "open_positions": open_count}

        # Check if we are still inside an already-scanned candle bucket while monitoring open positions
        scan_timeframe = getattr(self.strategy, "timeframe", None) or settings.R10_TIMEFRAME
        seconds_map = {
            "1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800,
            "1h": 3600, "2h": 7200, "4h": 14400, "6h": 21600, "8h": 28800,
            "12h": 43200, "1d": 86400, "3d": 259200, "1w": 604800,
        }
        tf_sec = seconds_map.get(str(scan_timeframe).lower(), 900)
        current_candle_bucket = int(time.time() // tf_sec)

        if self.last_scanned_bucket == current_candle_bucket and open_pos_items:
            # Active open positions are already checked in Section 1 on fast cadence.
            # Avoid redundant 100-pair REST requests on the same closed candle.
            self.last_action = f"Açık pozisyonlar izleniyor ({open_count} adet). Sonraki bar kapanışı bekleniyor."
            self._sync_runtime_state()
            return {"action": "MONITORING_OPEN_POSITIONS", "open_positions": open_count}

        # Dynamically screen Binance liquid USDT universe (> $10M 24h volume)
        universe = await dynamic_screener.get_liquid_universe()
        available_symbols = [s for s in universe if s not in broker.open_positions]

        if not available_symbols:
            add_system_log(f"🔄 Döngü #{self.cycle_count}: Taranacak uygun coin bulunamadı.", level="INFO", service="runner")
            self._sync_runtime_state()
            return {"action": "SCAN_COMPLETE", "open_positions": open_count}

        add_system_log(
            f"🔄 Döngü #{self.cycle_count}: Binance'teki tüm likit coinler ({len(available_symbols)} adet) eşzamanlı taranıyor...",
            level="INFO",
            service="runner"
        )

        scan_sem = asyncio.Semaphore(8)

        async def _scan_single_symbol(sym: str):
            async with scan_sem:
                try:
                    # 1. Adaptive Learning Cooldown Guard
                    from services.strategy_engine.adaptive_learning import adaptive_learning_engine
                    from services.analytics.trade_reflection import trade_reflection_engine

                    in_cd, cd_reason, rem_sec = adaptive_learning_engine.is_symbol_in_cooldown(sym)
                    if in_cd:
                        return {"symbol": sym, "status": "adaptive_cooldown", "reason": f"{cd_reason} ({rem_sec:.0f}s)"}

                    can_trade, shield_msg = btc_regime_shield.can_trade_symbol(sym, btc_health)
                    if not can_trade:
                        return {"symbol": sym, "status": "shield_blocked", "reason": shield_msg}

                    scan_timeframe = getattr(self.strategy, "timeframe", None) or settings.R10_TIMEFRAME
                    candles = await mds.get_historical_klines(sym, timeframe=scan_timeframe, limit=100)
                    if not candles or len(candles) < 30:
                        return {"symbol": sym, "status": "insufficient_data"}

                    latest_candle = candles[-1]
                    latest_candle_ts = latest_candle.timestamp

                    # Closed-candle deduplication guarantee (P0-CANDLE-DEDUP):
                    # Never evaluate or generate signals twice on the exact same closed candle.
                    if self.last_processed_candle_timestamp.get(sym) == latest_candle_ts:
                        return {
                            "symbol": sym,
                            "status": "already_processed",
                            "timestamp": latest_candle_ts,
                            "rsi": 50.0,
                            "last_price": float(latest_candle.close),
                        }

                    records = [
                        {
                            "timestamp": c.timestamp,
                            "open": c.open,
                            "high": c.high,
                            "low": c.low,
                            "close": c.close,
                            "volume": c.volume,
                        }
                        for c in candles
                    ]
                    df = pd.DataFrame(records)
                    sig = self.strategy.evaluate_from_dataframe(df, symbol=sym)

                    # Compute Opportunity Score hard gate (P1-005) & Adaptive Gate
                    if sig:
                        # Live emission timestamp: ensure signal is marked as freshly generated right now
                        sig.timestamp = datetime.now(timezone.utc)
                        sig.metadata["timeframe"] = scan_timeframe

                        vol_mean = df["volume"].rolling(20).mean().iloc[-1] if len(df) >= 20 else 1.0
                        vol_ratio = float(df["volume"].iloc[-1] / vol_mean) if vol_mean > 0 else 1.0
                        ind = {
                            "adx": float(df["adx"].iloc[-1]) if "adx" in df.columns and not pd.isna(df["adx"].iloc[-1]) else 25.0,
                            "volume_ratio": vol_ratio,
                            "bb_bandwidth": 0.04,
                            "bullish_divergence": 1.0 if sig.direction == SignalDirection.LONG else 0.0,
                            "bearish_divergence": 1.0 if sig.direction == SignalDirection.SHORT else 0.0,
                        }
                        candle_ts = candles[-1].timestamp if candles else datetime.now(timezone.utc)
                        features = FeatureVector(
                            symbol=sym,
                            timestamp=candle_ts,
                            timeframe=scan_timeframe,
                            indicators=ind,
                        )
                        regime_state = MarketRegimeState(
                            symbol=sym,
                            timeframe=scan_timeframe,
                            timestamp=candle_ts,
                            regime=MarketRegime.BULL_TREND if sig.direction == SignalDirection.LONG else MarketRegime.SIDEWAYS,
                            confidence=sig.confidence,
                        )
                        opp_score, opp_label = SignalScorer.calculate_opportunity_score(features, regime_state)

                        sig.opportunity_score = opp_score
                        sig.metadata["opportunity_score"] = opp_score
                        sig.metadata["opportunity_label"] = opp_label

                        min_opp = runtime_state.get(
                            "min_opportunity_score",
                            adaptive_learning_engine.get_min_opportunity_score()
                        )
                        if opp_score < min_opp:
                            sig = None  # Hard gate rejection
                        elif sig:
                            # Apply episodic post-trade memory insight
                            insight = trade_reflection_engine.get_relevant_insights(sym, sig.direction)
                            if insight:
                                sig.reason = f"{sig.reason} | [{insight}]"

                    last_p = float(candles[-1].close) if candles else 0.0
                    rsi_val = float(df["rsi"].iloc[-1]) if "rsi" in df.columns and not pd.isna(df["rsi"].iloc[-1]) else 50.0

                    self.last_processed_candle_timestamp[sym] = latest_candle_ts

                    return {
                        "symbol": sym,
                        "status": "ok",
                        "signal": sig,
                        "last_price": last_p,
                        "rsi": rsi_val,
                        "timestamp": candles[-1].timestamp if candles else None,
                    }
                except Exception as e:
                    return {"symbol": sym, "status": "error", "error": str(e)}

        scan_results = await asyncio.gather(*[_scan_single_symbol(s) for s in available_symbols], return_exceptions=True)
        self.last_scanned_bucket = current_candle_bucket

        confirmed_signals = []
        valid_rsi_list = []

        for res in scan_results:
            if isinstance(res, dict) and res.get("status") == "ok":
                valid_rsi_list.append((res["symbol"], res["rsi"], res["last_price"]))
                sig = res.get("signal")
                if sig:
                    confirmed_signals.append((res["symbol"], sig, res["timestamp"], res["last_price"]))

        # Report dip tracking (lowest 3 RSI coins) for real market visibility
        valid_rsi_list.sort(key=lambda x: x[1])
        lowest_3 = valid_rsi_list[:3]
        if lowest_3:
            rsi_summary = ", ".join([f"{sym}: RSI={r:.1f} (${p:,.2f})" for sym, r, p in lowest_3])
            add_system_log(f"📊 Dip Takibi (En Düşük RSI): {rsi_summary}", level="INFO", service="strategy")

        executed_orders_count = 0
        if confirmed_signals:
            confirmed_signals.sort(key=lambda x: x[1].confidence, reverse=True)

            for sym, sig, candle_ts, last_p in confirmed_signals:
                sig_price = getattr(sig, "entry_price", last_p)
                price_str = f"${sig_price:,.4f}" if sig_price < 1.0 else f"${sig_price:,.2f}"
                strat_tag = getattr(sig, "strategy", "STRATEGY").upper()
                add_system_log(
                    f"🎯 {sym}: {strat_tag} {sig.direction.value} SİNYALİ TESPİT EDİLDİ! Fiyat: {price_str} | Güven: {sig.confidence:.2f} | {sig.reason}",
                    level="SUCCESS",
                    service="strategy"
                )

                # Shadow Signal Gate Evaluation & Atomic Decision Snapshot
                sig_id = getattr(sig, "signal_id", None) or f"SIG_{sym.replace('/', '_')}_{int(datetime.now(timezone.utc).timestamp() * 1000)}"
                try:
                    sig.signal_id = sig_id
                except Exception:
                    pass
                gate_eval = None
                try:
                    from services.learning.signal_gate_engine import signal_gate_engine
                    gate_eval = signal_gate_engine.evaluate_signal_shadow(
                        signal=sig,
                        market_regime=current_context,
                    )
                except Exception as shadow_err:
                    logger.warning(f"Shadow signal gate evaluation failed: {shadow_err}")

                decision_id = getattr(gate_eval, "decision_id", None) if gate_eval else f"dec_{sig_id}"

                if self.risk_engine.is_spot_mode and sig.direction == SignalDirection.SHORT:
                    try:
                        from services.learning.decision_logger import decision_logger
                        decision_logger.log_risk_decision(decision_id, False, "SPOT_MODE_NO_SHORT")
                    except Exception:
                        pass
                    add_system_log(
                        f"🛡️ {sym}: SHORT sinyali tespit edildi fakat Spot Modda açığa satış engellendi (Yalnızca İzleme)",
                        level="WARNING",
                        service="risk"
                    )
                    continue

                current_open = len(broker.open_positions)
                if current_open >= max_allowed:
                    try:
                        from services.learning.decision_logger import decision_logger
                        decision_logger.log_risk_decision(decision_id, False, f"MAX_POSITIONS_REACHED_{current_open}/{max_allowed}")
                    except Exception:
                        pass
                    add_system_log(
                        f"⚠️ {sym}: Alım sinyali var ancak maksimum pozisyon sayısına ({current_open}/{max_allowed}) ulaşıldı.",
                        level="WARNING",
                        service="risk"
                    )
                    continue

                portfolio_state = broker.to_portfolio_state()
                sig.timestamp = datetime.now(timezone.utc)
                decision = self.risk_engine.evaluate_signal(
                    signal=sig,
                    portfolio=portfolio_state,
                    latest_market_time=datetime.now(timezone.utc),
                    closed_positions=closed_history,
                )

                try:
                    from services.learning.decision_logger import decision_logger
                    decision_logger.log_risk_decision(decision_id, decision.approved, decision.reason)
                except Exception:
                    pass

                if decision.approved:
                    # L2 Order Book Imbalance (OBI) & Spread Microstructure Guard (Hummingbot/HaasOnline model)
                    try:
                        ob = await mds.get_orderbook(sym, limit=20)
                        if ob:
                            obi_metrics = mds.calculate_orderbook_imbalance(ob)
                            obi = obi_metrics["obi"]
                            spread_bps = obi_metrics["spread_bps"]
                            max_spread = getattr(settings, "MAX_SPREAD_BPS", 25.0)

                            if spread_bps > max_spread:
                                reject_msg = f"⛔ {sym}: Tahta makası çok geniş ({spread_bps:.1f} bps > {max_spread:.1f} bps). İşlem iptal edildi."
                                add_system_log(reject_msg, level="WARNING", service="risk")
                                try:
                                    from services.learning.decision_logger import decision_logger
                                    decision_logger.log_risk_decision(decision_id, False, f"SPREAD_TOO_WIDE_{spread_bps:.1f}bps")
                                except Exception:
                                    pass
                                continue

                            if sig.direction == SignalDirection.LONG and obi < -0.25:
                                reject_msg = f"⛔ {sym}: L2 Tahta baskısı satıcı ağırlıklı (OBI: {obi:.2f} < -0.25). Alım iptal edildi."
                                add_system_log(reject_msg, level="WARNING", service="risk")
                                try:
                                    from services.learning.decision_logger import decision_logger
                                    decision_logger.log_risk_decision(decision_id, False, f"OBI_SELLER_PRESSURE_{obi:.2f}")
                                except Exception:
                                    pass
                                continue
                    except Exception as e:
                        logger.warning(f"L2 orderbook check skipped for {sym}: {e}")

                    order_manager.bind_execution_engine(broker)
                    order_manager.bind_risk_engine(self.risk_engine)

                    sig_id = getattr(sig, "signal_id", None) or f"SIG_{sym.replace('/', '_')}_{int(datetime.now().timestamp())}"
                    try:
                        order, fill, pos = await order_manager.execute_risk_decision(
                            decision=decision,
                            strategy_name=sig.strategy,
                            signal_id=sig_id,
                            entry_context=current_context,
                            entry_arm=current_arm_id,
                        )

                        try:
                            from services.learning.decision_logger import decision_logger
                            decision_logger.link_trade_execution(
                                signal_id=sig_id,
                                trade_id=pos.position_id,
                                initial_risk_amount=abs(pos.entry_price - pos.stop_loss) * pos.quantity,
                            )
                        except Exception as link_err:
                            logger.warning(f"Failed to link trade execution to decision logger: {link_err}")

                        side_str = "LONG" if sig.direction == SignalDirection.LONG else "SHORT"
                        msg = f"⚡ EMİR AÇILDI: {sym} {side_str} @ ${pos.entry_price:,.2f} (SL: ${pos.stop_loss:,.2f}, TP: ${pos.take_profit:,.2f})"
                        self.last_action = msg
                        add_system_log(msg, level="SUCCESS", service="execution")
                        asyncio.create_task(telegram_service.notify_trade_open(
                            pos={"symbol": sym, "side": side_str, "quantity": pos.quantity, "entry_price": pos.entry_price, "stop_loss": pos.stop_loss, "take_profit": pos.take_profit}
                        ))
                        logger.info(msg)
                        if self.command_bus and hasattr(self.command_bus, "_log_audit"):
                            self.command_bus._log_audit(
                                action="PAPER_ORDER_OPENED",
                                parameters={"symbol": sym, "side": side_str, "qty": pos.quantity, "entry_price": pos.entry_price, "stop_loss": pos.stop_loss, "take_profit": pos.take_profit},
                                success=True,
                                result={"order_id": order.order_id, "position_id": pos.position_id, "equity": broker.equity},
                            )
                        open_count += 1
                        executed_orders_count += 1
                    except Exception as exec_err:
                        err_msg = f"⚠️ {sym}: Emir iletim hatası: {exec_err}"
                        logger.warning(err_msg)
                        add_system_log(err_msg, level="WARNING", service="execution")
                else:
                    reject_msg = f"⛔ {sym}: RiskEngine emri reddetti: {decision.reason}"
                    add_system_log(reject_msg, level="WARNING", service="risk")
                    logger.info(f"Autonomous Runner: RiskEngine {sym} sinyalini reddetti: {decision.reason}")

        complete_msg = (
            f"✅ Döngü #{self.cycle_count} tamamlandı: {len(available_symbols)} coin tarandı "
            f"({len(confirmed_signals)} sinyal, {executed_orders_count} yeni emir, {open_count}/{max_allowed} açık pozisyon). "
            f"Sonraki tarama {self.cycle_interval}s sonra."
        )
        self.last_action = complete_msg
        add_system_log(complete_msg, level="INFO", service="runner")
        self._sync_runtime_state()
        return {"action": "SCAN_COMPLETE", "open_positions": open_count, "executed": executed_orders_count}

    def _sync_runtime_state(self):
        if not self.command_bus or not getattr(self.command_bus, "broker", None):
            return
        broker = self.command_bus.broker
        runtime_state = getattr(self.command_bus, "runtime_state", None)
        if runtime_state is None:
            return

        runtime_state["balance"] = round(broker.balance, 2)
        runtime_state["equity"] = broker.equity
        runtime_state["unrealized_pnl"] = round(broker.total_unrealized_pnl, 2)
        runtime_state["realized_pnl"] = round(broker.total_realized_pnl, 2)
        runtime_state["daily_pnl"] = getattr(broker, "daily_pnl", round(broker.total_unrealized_pnl + broker.total_realized_pnl, 2))
        runtime_state["open_positions_count"] = len(broker.open_positions)
        runtime_state["last_cycle_at"] = self.last_cycle_at
        runtime_state["last_action"] = self.last_action
        runtime_state["is_autonomous_active"] = self.is_active
        runtime_state["worker_state"] = self.worker_state.value
        if self.worker_state == TradingWorkerState.RUNNING:
            runtime_state["system_state"] = "TRADING"
            runtime_state["is_halted"] = False
        elif self.worker_state == TradingWorkerState.EMERGENCY_STOP:
            runtime_state["system_state"] = "EMERGENCY_SHUTDOWN"
            runtime_state["is_halted"] = True
            runtime_state["circuit_state"] = "LOCKED"
        elif self.worker_state == TradingWorkerState.STOPPED:
            if runtime_state.get("system_state") == "TRADING":
                runtime_state["system_state"] = "READY"


autonomous_trader = AutonomousPaperTrader()
