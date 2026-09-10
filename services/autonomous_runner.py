import asyncio
import json
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

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
        add_system_log("▶️ OTONOM BOT DEVAM ETTİRİLDİ (RUNNING).", level="SUCCESS", service="runner")

    def emergency_stop(self, reason: str = "Acil durum emri verildi."):
        self.worker_state = TradingWorkerState.EMERGENCY_STOP
        self.is_active = False
        self.last_action = f"🚨 ACİL DURDURMA: {reason}"
        self._release_worker_lock(self._lock_token)
        self._lock_token = None
        global _ACTIVE_RUNNER_INSTANCE
        if _ACTIVE_RUNNER_INSTANCE is self:
            _ACTIVE_RUNNER_INSTANCE = None
        if self._task and not self._task.done():
            self._task.cancel()
            self._task = None
        if self.command_bus and getattr(self.command_bus, "broker", None):
            self.command_bus.broker.emergency_close_all()
        self._sync_runtime_state()
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

        if existing_pid > 0 and _is_pid_alive(existing_pid):
            raise RuntimeError(
                f"Only one trading loop can run at a time: another loop is already active in process PID {existing_pid}."
            )

        # Stale lock from deceased process -> clean and retry
        logger.warning(f"Removing stale worker lock from deceased PID {existing_pid}")
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

    async def _run_loop(self):
        await asyncio.sleep(1)
        while True:
            try:
                if self.is_active:
                    await self.step_cycle()
                await asyncio.sleep(self.cycle_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in autonomous trader loop: {e}", exc_info=True)
                await asyncio.sleep(5)

    async def step_cycle(self) -> Dict[str, Any]:
        self.cycle_count += 1
        self.last_cycle_at = datetime.now(timezone.utc).strftime("%H:%M:%S")

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
        # 1. Update prices of open positions with REAL Binance market data
        # ---------------------------------------------------------------------
        for symbol, pos in list(broker.open_positions.items()):
            if pos.status == PositionStatus.OPEN:
                try:
                    ticker = await mds.get_live_ticker(symbol)
                    if ticker and "price" in ticker and ticker["price"] > 0:
                        real_price = float(ticker["price"])
                        broker.update_market_price(symbol, real_price)

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
                            self.last_action = f"{symbol} {reason} tetiklendi. Kâr/Zarar: ${closed_pos.realized_pnl:+.2f}"
                            logger.info(self.last_action)
                            asyncio.create_task(telegram_service.notify_trade_close(
                                pos={"symbol": symbol, "current_price": exit_price, "realized_pnl": closed_pos.realized_pnl, "fees_paid": getattr(closed_pos, "commission_paid", 0.0)},
                                reason=reason
                            ))
                            if self.command_bus and hasattr(self.command_bus, "_log_audit"):
                                self.command_bus._log_audit(
                                    action="PAPER_POSITION_CLOSED",
                                    parameters={"symbol": symbol, "reason": reason, "exit_price": exit_price, "realized_pnl": closed_pos.realized_pnl},
                                    success=True,
                                    result={"balance": broker.balance, "equity": broker.equity},
                                )
                            self._sync_runtime_state()
                            return {
                                "action": "POSITION_CLOSED",
                                "reason": reason,
                                "symbol": symbol,
                                "pnl": closed_pos.realized_pnl,
                            }
                        elif not was_partial and getattr(pos, "partial_tp_hit", False):
                            partial_pnl = getattr(pos, "partial_realized_pnl", 0.0)
                            self.last_action = (
                                f"🎯 {symbol} 1R Kısmi Kâr Alındı (%50). Realize Kâr: +${partial_pnl:.2f}. "
                                f"Kalan pozisyon Stop Loss seviyesi başabaşa (${pos.stop_loss:.2f}) çekildi."
                            )
                            logger.info(self.last_action)
                            add_system_log(self.last_action, level="INFO", service="execution")
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
                            self._sync_runtime_state()
                            return {
                                "action": "POSITION_PARTIAL_EXIT",
                                "symbol": symbol,
                                "pnl": partial_pnl,
                                "remaining_qty": pos.quantity,
                            }
                except Exception as e:
                    logger.warning(f"Failed to fetch live price for open position {symbol}: {e}")

        # ---------------------------------------------------------------------
        # 1.5 Mathematical Expectancy Circuit Breaker ("Negatife dönerse hemen dur")
        # ---------------------------------------------------------------------
        closed_history = getattr(broker, "closed_positions_history", [])
        exp_tripped, exp_reason, _ = self.risk_engine.circuit_breaker.check_expectancy(closed_history)

        if exp_tripped:
            halt_msg = f"🛑 {exp_reason}"
            self.last_action = halt_msg
            self.is_active = False
            runtime_state["circuit_state"] = "EXPECTANCY_HALTED"
            runtime_state["is_autonomous_active"] = False
            add_system_log(halt_msg, level="ERROR", service="risk")
            self._sync_runtime_state()
            return {"action": "EXPECTANCY_HALTED", "reason": exp_reason}

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
        # 2. Evaluate Strategy and Risk Engine for New Positions
        # ---------------------------------------------------------------------
        open_count = len(broker.open_positions)
        max_allowed = int(runtime_state.get("max_open_positions", self.risk_engine.max_open_positions))

        if open_count >= max_allowed:
            self.last_action = f"Maksimum açık pozisyon doldu ({open_count}/{max_allowed}). Sadece açık pozisyonlar izleniyor."
            self._sync_runtime_state()
            return {"action": "MONITORING_MAX_CAPACITY", "open_positions": open_count}

        r10_enabled = getattr(self.strategy, "enabled", True)
        if not r10_enabled:
            self.last_action = "R10 RSI Divergence stratejisi devre dışı. Sadece açık pozisyonlar izleniyor."
            add_system_log(
                f"⏸️ R10 Stratejisi devre dışı. Yeni sinyal taranmıyor, mevcut {open_count} pozisyon izleniyor.",
                level="INFO",
                service="runner",
            )
            self._sync_runtime_state()
            return {"action": "STRATEGY_DISABLED", "open_positions": open_count}

        # ---------------------------------------------------------------------
        # 2.1 Evaluate BTC Trend Shield (Regime Filter)
        # ---------------------------------------------------------------------
        btc_candles = await mds.get_historical_klines("BTC/USDT", timeframe="15m", limit=30)
        btc_health = btc_regime_shield.evaluate_btc_health(btc_candles)
        if btc_health.is_dumping:
            add_system_log(f"🛡️ BTC TREND KALKANI AKTİF: {btc_health.reason}. Altcoin alımları donduruldu.", level="WARNING", service="risk")

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

        scan_sem = asyncio.Semaphore(4)

        async def _scan_single_symbol(sym: str):
            async with scan_sem:
                try:
                    can_trade, shield_msg = btc_regime_shield.can_trade_symbol(sym, btc_health)
                    if not can_trade:
                        return {"symbol": sym, "status": "shield_blocked", "reason": shield_msg}

                    scan_timeframe = getattr(self.strategy, "timeframe", None) or settings.R10_TIMEFRAME
                    candles = await mds.get_historical_klines(sym, timeframe=scan_timeframe, limit=100)
                    if not candles or len(candles) < 30:
                        return {"symbol": sym, "status": "insufficient_data"}

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
                    sig = None
                    if r10_enabled:
                        sig = self.strategy.evaluate_from_dataframe(df, symbol=sym)

                    # Compute Opportunity Score hard gate (P1-005)
                    if sig:
                        from services.signal_engine.scorer import SignalScorer
                        from shared.schemas import FeatureVector, MarketRegimeState
                        vol_mean = df["volume"].rolling(20).mean().iloc[-1] if len(df) >= 20 else 1.0
                        vol_ratio = float(df["volume"].iloc[-1] / vol_mean) if vol_mean > 0 else 1.0
                        ind = {
                            "adx": float(df["adx"].iloc[-1]) if "adx" in df.columns and not pd.isna(df["adx"].iloc[-1]) else 25.0,
                            "volume_ratio": vol_ratio,
                            "bb_bandwidth": 0.04,
                            "bullish_divergence": 1.0 if sig.direction == SignalDirection.LONG else 0.0,
                            "bearish_divergence": 1.0 if sig.direction == SignalDirection.SHORT else 0.0,
                        }
                        features = FeatureVector(
                            symbol=sym,
                            timestamp=candles[-1].timestamp if candles else datetime.now(timezone.utc),
                            timeframe=scan_timeframe,
                            indicators=ind,
                        )
                        regime_state = MarketRegimeState(
                            regime=MarketRegime.TRENDING_BULL if sig.direction == SignalDirection.LONG else MarketRegime.RANGING
                        )
                        opp_score, opp_label = SignalScorer.calculate_opportunity_score(features, regime_state)
                        sig.opportunity_score = opp_score
                        sig.metadata["opportunity_score"] = opp_score
                        sig.metadata["opportunity_label"] = opp_label

                        min_opp = runtime_state.get("min_opportunity_score", getattr(settings, "MIN_OPPORTUNITY_SCORE", 50.0))
                        if opp_score < min_opp:
                            sig = None  # Hard gate rejection

                    last_p = float(candles[-1].close) if candles else 0.0
                    rsi_val = float(df["rsi"].iloc[-1]) if "rsi" in df.columns and not pd.isna(df["rsi"].iloc[-1]) else 50.0

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

        scan_results = await asyncio.gather(*[_scan_single_symbol(s) for s in available_symbols])

        confirmed_signals = []
        valid_rsi_list = []

        for res in scan_results:
            if res.get("status") == "ok":
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

                if self.risk_engine.is_spot_mode and sig.direction == SignalDirection.SHORT:
                    add_system_log(
                        f"🛡️ {sym}: SHORT sinyali tespit edildi fakat Spot Modda açığa satış engellendi (Yalnızca İzleme)",
                        level="WARNING",
                        service="risk"
                    )
                    continue

                if open_count >= max_allowed:
                    add_system_log(
                        f"⚠️ {sym}: Alım sinyali var ancak maksimum pozisyon sayısına ({open_count}/{max_allowed}) ulaşıldı.",
                        level="WARNING",
                        service="risk"
                    )
                    continue

                portfolio_state = broker.to_portfolio_state()
                decision = self.risk_engine.evaluate_signal(
                    signal=sig,
                    portfolio=portfolio_state,
                    latest_market_time=datetime.now(timezone.utc),
                    closed_positions=closed_history,
                )

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
                                continue

                            if sig.direction == SignalDirection.LONG and obi < -0.25:
                                reject_msg = f"⛔ {sym}: L2 Tahta baskısı satıcı ağırlıklı (OBI: {obi:.2f} < -0.25). Alım iptal edildi."
                                add_system_log(reject_msg, level="WARNING", service="risk")
                                continue
                    except Exception as e:
                        logger.warning(f"L2 orderbook check skipped for {sym}: {e}")

                    from services.execution.order_manager import order_manager
                    order_manager.bind_execution_engine(broker)
                    order_manager.bind_risk_engine(self.risk_engine)

                    sig_id = getattr(sig, "signal_id", None) or f"SIG_{sym.replace('/', '_')}_{int(datetime.now().timestamp())}"
                    order, fill, pos = await order_manager.execute_risk_decision(
                        decision=decision,
                        strategy_name=sig.strategy,
                        signal_id=sig_id,
                    )

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
        import gc
        gc.collect()
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
