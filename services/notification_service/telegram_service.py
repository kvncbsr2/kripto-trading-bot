from typing import Any, Dict

import httpx

from shared.config import get_settings
from shared.logging import get_logger

logger = get_logger("telegram-service", service="notification")


class TelegramNotificationService:
    def __init__(self):
        settings = get_settings()
        self.bot_token = settings.TELEGRAM_BOT_TOKEN
        self.chat_id = settings.TELEGRAM_CHAT_ID
        self.enabled = settings.TELEGRAM_NOTIFICATIONS_ENABLED and bool(
            self.bot_token and self.chat_id
        )
        self.trading_halted = False

    async def send_message(self, message: str) -> bool:
        if not self.enabled:
            logger.info(f"[Telegram Mock] {message}")
            return True

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": self.chat_id,
            "text": message,
            "parse_mode": "HTML",
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(url, json=payload)
                return resp.status_code == 200
        except Exception as e:
            logger.error(f"Failed to send Telegram alert: {e}")
            return False

    async def notify_signal(self, signal: Dict[str, Any]) -> bool:
        msg = (
            f"🚨 <b>NEW SIGNAL GENERATED</b>\n"
            f"Symbol: <code>{signal.get('symbol')}</code>\n"
            f"Direction: <b>{signal.get('direction')}</b>\n"
            f"Strategy: {signal.get('strategy')}\n"
            f"Entry: ${signal.get('entry_price', 0):.2f}\n"
            f"Stop-Loss: ${signal.get('stop_price', 0):.2f}\n"
            f"Take-Profit: ${signal.get('take_profit', 0):.2f}\n"
            f"Confidence: {signal.get('confidence', 0):.2f}"
        )
        return await self.send_message(msg)

    async def notify_trade_open(self, pos: Dict[str, Any]) -> bool:
        msg = (
            f"🟢 <b>PAPER POSITION OPENED</b>\n"
            f"Symbol: <code>{pos.get('symbol')}</code> ({pos.get('side')})\n"
            f"Size: {pos.get('quantity')}\n"
            f"Entry: ${pos.get('entry_price', 0):.2f}\n"
            f"SL: ${pos.get('stop_loss', 0):.2f} | TP: ${pos.get('take_profit', 0):.2f}"
        )
        return await self.send_message(msg)

    async def notify_trade_close(self, pos: Dict[str, Any], reason: str) -> bool:
        icon = "🎯" if reason == "TAKE_PROFIT" else "🛑"
        msg = (
            f"{icon} <b>PAPER POSITION CLOSED [{reason}]</b>\n"
            f"Symbol: <code>{pos.get('symbol')}</code>\n"
            f"Exit: ${pos.get('current_price', 0):.2f}\n"
            f"Realized PnL: <b>${pos.get('realized_pnl', 0):.2f}</b>\n"
            f"Fees: ${pos.get('fees_paid', 0):.2f}"
        )
        return await self.send_message(msg)

    async def notify_circuit_breaker(self, reason: str):
        msg = (
            f"⚠️ <b>CIRCUIT BREAKER TRIGGERED</b>\n"
            f"State: <b>LOCKED</b>\n"
            f"Reason: {reason}\n"
            f"Trading is HALTED."
        )
        await self.send_message(msg)

    async def notify_bot_started(self, capital: float = 5000.0) -> bool:
        msg = (
            f"▶️ <b>KRIPTO AGENT BAŞLATILDI</b>\n"
            f"Mod: <b>Paper Trading (0 Risk)</b>\n"
            f"Başlangıç Sermayesi: <b>${capital:,.2f}</b>\n"
            f"Strateji: <b>R10 RSI Uyumsuzluğu</b>\n"
            f"Piyasa: <b>Binance Spot Canlı</b>"
        )
        return await self.send_message(msg)

    async def notify_bot_stopped(self) -> bool:
        msg = "⏹️ <b>KRIPTO AGENT DURDURULDU</b>\nPiyasa taraması duraklatıldı."
        return await self.send_message(msg)

    def handle_command(self, cmd: str, system_state: Dict[str, Any]) -> str:
        cmd = cmd.strip().lower()
        if cmd == "/status":
            return f"System: {'HALTED' if self.trading_halted else 'ACTIVE'}\nEquity: ${system_state.get('equity', 5000):.2f}\nToday PnL: ${system_state.get('daily_pnl', 0):.2f}"
        elif cmd == "/balance":
            return f"Balance: ${system_state.get('balance', 5000):.2f}\nEquity: ${system_state.get('equity', 5000):.2f}"
        elif cmd == "/stop":
            self.trading_halted = True
            return "⛔ Trading manually HALTED. No new positions will be opened."
        elif cmd == "/resume":
            self.trading_halted = False
            return "✅ Trading RESUMED."
        elif cmd == "/positions":
            positions = system_state.get("open_positions", [])
            if not positions:
                return "No open positions."
            return "\n".join(
                [f"{p.get('symbol')} {p.get('side')} Qty: {p.get('quantity')}" for p in positions]
            )
        return f"Unknown command: {cmd}"


telegram_service = TelegramNotificationService()
