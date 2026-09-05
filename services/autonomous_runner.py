import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import pandas as pd

from services.feature_engine.features import FeatureEngine
from services.market_data.dynamic_screener import dynamic_screener
from services.market_data.market_data_service import MarketDataService
from services.notification_service.telegram_service import telegram_service
from services.risk_engine.btc_regime_shield import btc_regime_shield
from services.risk_engine.risk_engine import RiskEngine
from services.strategy_engine.strategies.r10_rsi_divergence import (
    R10RSIDivergenceStrategy,
    create_r10_strategy_from_settings,
)
from shared.config import get_settings
from shared.enums import PositionStatus, SignalDirection
from shared.logging import add_system_log, get_logger

logger = get_logger("autonomous-trader", service="autonomous_trader")
settings = get_settings()

WATCH_SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "DOGE/USDT"]


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
        )
        self.strategy = create_r10_strategy_from_settings()
        self.feature_engine = FeatureEngine()

        # Requirement 4: Starts in OFF state by default
        self.is_active: bool = False
        self.cycle_interval: int = 15
        self.cycle_count: int = 0
        self.last_cycle_at: Optional[str] = None
        self.last_action: str = "Otonom motor beklemede (OFF). Kullanıcı başlatması bekleniyor."
        self._task: Optional[asyncio.Task] = None

    def bind_command_bus(self, command_bus_instance):
        self.command_bus = command_bus_instance
        if hasattr(command_bus_instance, "strategy_manager") and command_bus_instance.strategy_manager:
            r10 = next((s for s in command_bus_instance.strategy_manager.strategies if s.name == "r10_rsi_divergence"), None)
            if r10:
                self.strategy = r10

    def bind_market_data_service(self, market_data_service: MarketDataService):
        self.market_data_service = market_data_service

    def _ensure_market_data_service(self) -> MarketDataService:
        if self.market_data_service is None:
            self.market_data_service = MarketDataService(symbols=WATCH_SYMBOLS)
        return self.market_data_service

    def start(self):
        self.is_active = True
        self.last_action = "Otonom motor aktif. Gerçek piyasa taranıyor."
        add_system_log("▶️ OTONOM BOT BAŞLATILDI: Canlı Binance Spot kline/fiyat taraması aktif.", level="SUCCESS", service="runner")
        asyncio.create_task(telegram_service.notify_bot_started(5000.0))
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run_loop())
            logger.info("Autonomous Paper Trader loop started (ACTIVE).")

    def stop(self):
        self.is_active = False
        self.last_action = "Otonom motor durduruldu (OFF)."
        add_system_log("⏹️ OTONOM BOT DURDURULDU: Alım-satım taraması duraklatıldı.", level="WARNING", service="runner")
        asyncio.create_task(telegram_service.notify_bot_stopped())
        if self._task and not self._task.done():
            self._task.cancel()
            self._task = None
            logger.info("Autonomous Paper Trader loop stopping (INACTIVE).")

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
                except Exception as e:
                    logger.warning(f"Failed to fetch live price for open position {symbol}: {e}")

        # ---------------------------------------------------------------------
        # 2. Evaluate Strategy and Risk Engine for New Positions
        # ---------------------------------------------------------------------
        open_count = len(broker.open_positions)
        max_allowed = int(runtime_state.get("max_open_positions", self.risk_engine.max_open_positions))

        if open_count >= max_allowed:
            self.last_action = f"Maksimum açık pozisyon doldu ({open_count}/{max_allowed}). Sadece açık pozisyonlar izleniyor."
            self._sync_runtime_state()
            return {"action": "MONITORING_MAX_CAPACITY", "open_positions": open_count}

        if not getattr(self.strategy, "enabled", True):
            self.last_action = f"Strateji ({self.strategy.name}) devre dışı. Sadece açık pozisyonlar izleniyor."
            add_system_log(
                f"⏸️ Strateji ({self.strategy.name}) devre dışı. Yeni sinyal taranmıyor, mevcut {open_count} pozisyon izleniyor.",
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

        scan_sem = asyncio.Semaphore(10)

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
                    sig = self.strategy.evaluate_from_dataframe(df, symbol=sym)
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
                add_system_log(
                    f"🎯 {sym}: R10 {sig.direction.value} SİNYALİ TESPİT EDİLDİ! Fiyat: ${sig_price:,.2f} | Güven: {sig.confidence:.2f} | {sig.reason}",
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
                    latest_market_time=candle_ts,
                )

                if decision.approved:
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


autonomous_trader = AutonomousPaperTrader()
