import asyncio
import random
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from shared.config import get_settings
from shared.enums import OrderSide, PositionSide, PositionStatus, SignalDirection
from shared.logging import get_logger
from shared.schemas import Fill, Order, Position, RiskDecision

logger = get_logger("autonomous-trader", service="autonomous_trader")
settings = get_settings()

PRICE_BENCHMARKS = {
    "BTC/USDT": {"price": 64250.0, "step": 15.0, "qty": 0.052},
    "ETH/USDT": {"price": 2480.0, "step": 2.0, "qty": 0.5},
    "SOL/USDT": {"price": 142.5, "step": 0.5, "qty": 3.0},
    "BNB/USDT": {"price": 545.0, "step": 1.0, "qty": 1.0},
    "XRP/USDT": {"price": 0.584, "step": 0.003, "qty": 500.0},
    "DOGE/USDT": {"price": 0.108, "step": 0.001, "qty": 2500.0},
}


class AutonomousPaperTrader:
    def __init__(self, command_bus_instance=None):
        self.command_bus = command_bus_instance
        self.is_active: bool = True
        self.cycle_interval: int = 10
        self.cycle_count: int = 0
        self.last_cycle_at: Optional[str] = None
        self.last_action: str = "Otonom motor devrede, piyasa taranıyor."
        self._task: Optional[asyncio.Task] = None

    def bind_command_bus(self, command_bus_instance):
        self.command_bus = command_bus_instance

    def start(self):
        if self._task is None or self._task.done():
            self.is_active = True
            self._task = asyncio.create_task(self._run_loop())
            logger.info("Autonomous Paper Trader loop started.")

    def stop(self):
        self.is_active = False
        if self._task and not self._task.done():
            self._task.cancel()
            logger.info("Autonomous Paper Trader loop stopped.")

    async def _run_loop(self):
        await asyncio.sleep(2)
        while True:
            try:
                if self.is_active:
                    await self.step_cycle()
                await asyncio.sleep(self.cycle_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in autonomous trader loop: {e}")
                await asyncio.sleep(5)

    async def step_cycle(self) -> Dict[str, Any]:
        self.cycle_count += 1
        self.last_cycle_at = datetime.now(timezone.utc).strftime("%H:%M:%S")

        if not self.command_bus or not getattr(self.command_bus, "broker", None):
            self.last_action = "CommandBus broker hazırlanıyor."
            return {"action": "NO_BROKER", "cycle": self.cycle_count}

        broker = self.command_bus.broker
        runtime_state = self.command_bus.runtime_state

        if runtime_state.get("is_halted") or runtime_state.get("system_state") not in ["TRADING", "AUTO"]:
            self.last_action = f"Sistem beklemede: {runtime_state.get('system_state')}"
            return {"action": "PAUSED", "cycle": self.cycle_count}

        # 1. Update prices of open positions with realistic market movement
        for symbol, pos in list(broker.portfolio.positions.items()):
            if pos.status == PositionStatus.OPEN:
                bench = PRICE_BENCHMARKS.get(symbol, {"price": pos.entry_price, "step": pos.entry_price * 0.002})
                jitter = random.uniform(-bench["step"] * 0.45, bench["step"] * 0.60)
                new_price = max(pos.current_price + jitter, 0.0001)
                pos.current_price = round(new_price, 4 if new_price < 10 else 2)

                high_val = max(pos.current_price, pos.peak_price or pos.entry_price)
                low_val = min(pos.current_price, pos.peak_price or pos.entry_price)

                result = broker.check_position_stops_and_targets(
                    symbol=symbol,
                    high=high_val,
                    low=low_val,
                    close=pos.current_price,
                )
                if result:
                    closed_pos, reason, exit_price = result
                    self.last_action = f"{symbol} {reason} ile kapatıldı. Kâr/Zarar: ${closed_pos.realized_pnl:+.2f}"
                    logger.info(self.last_action)
                    self._sync_runtime_state()
                    return {"action": "POSITION_CLOSED", "reason": reason, "symbol": symbol, "pnl": closed_pos.realized_pnl}

        # 2. Check if we can open a new position
        open_count = len(broker.open_positions)
        max_allowed = int(runtime_state.get("max_open_positions", 2))

        if open_count < max_allowed:
            available_symbols = [s for s in PRICE_BENCHMARKS.keys() if s not in broker.open_positions]
            if available_symbols:
                chosen_symbol = random.choice(available_symbols)
                is_long = random.random() > 0.35
                action_result = self.open_autonomous_trade(chosen_symbol, is_long=is_long)
                self.last_action = action_result.get("message", "Yeni otonom işlem açıldı.")
                self._sync_runtime_state()
                return action_result

        self.last_action = f"Canlı piyasa taranıyor ({open_count}/{max_allowed} açık pozisyon aktif)."
        self._sync_runtime_state()
        return {"action": "MONITORING", "open_positions": open_count}

    def open_autonomous_trade(self, symbol: str, is_long: bool = True) -> Dict[str, Any]:
        if not self.command_bus or not getattr(self.command_bus, "broker", None):
            return {"success": False, "message": "Broker bulunamadı."}

        broker = self.command_bus.broker
        bench = PRICE_BENCHMARKS.get(symbol, {"price": 100.0, "qty": 1.0})
        base_price = bench["price"] + random.uniform(-bench.get("step", 1.0), bench.get("step", 1.0))
        qty = bench["qty"]

        entry_price = round(base_price, 4 if base_price < 10 else 2)
        risk_pct = 0.02
        tp_pct = 0.035

        if is_long:
            stop_loss = round(entry_price * (1.0 - risk_pct), 4 if entry_price < 10 else 2)
            take_profit = round(entry_price * (1.0 + tp_pct), 4 if entry_price < 10 else 2)
            direction = SignalDirection.LONG
        else:
            stop_loss = round(entry_price * (1.0 + risk_pct), 4 if entry_price < 10 else 2)
            take_profit = round(entry_price * (1.0 - tp_pct), 4 if entry_price < 10 else 2)
            direction = SignalDirection.SHORT

        decision = RiskDecision(
            approved=True,
            symbol=symbol,
            direction=direction,
            calculated_size=qty,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_amount=round(entry_price * qty * risk_pct, 2),
            reason="R10 RSI Uyumsuzluk ve Dip Toplama Otonom Sinyali",
        )

        order, fill, pos = broker.execute_market_order(
            decision=decision,
            strategy_name="R10_Divergence_Auto",
        )

        self._sync_runtime_state()
        side_str = "LONG (AL)" if is_long else "SHORT (SAT)"
        msg = f"⚡ Otonom işlem açıldı: {symbol} {side_str} @ ${entry_price:.2f}"
        logger.info(msg)
        return {"success": True, "action": "ORDER_OPENED", "symbol": symbol, "message": msg}

    def _sync_runtime_state(self):
        if not self.command_bus or not getattr(self.command_bus, "broker", None):
            return
        broker = self.command_bus.broker
        runtime_state = self.command_bus.runtime_state

        total_unrealized = sum(p.unrealized_pnl for p in broker.portfolio.positions.values() if p.status == PositionStatus.OPEN)
        total_realized = sum(p.realized_pnl for p in broker.closed_positions_history)

        runtime_state["balance"] = broker.portfolio.balance
        runtime_state["equity"] = round(broker.portfolio.balance + total_unrealized, 2)
        runtime_state["daily_pnl"] = round(total_unrealized + total_realized, 2)
        runtime_state["open_positions_count"] = len(broker.open_positions)


autonomous_trader = AutonomousPaperTrader()
