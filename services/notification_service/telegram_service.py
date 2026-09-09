import asyncio
from typing import Any, Dict, Optional

import httpx

from shared.config import get_settings
from shared.logging import get_logger

logger = get_logger("telegram-service", service="notification")


class TelegramNotificationService:
    def __init__(self):
        settings = get_settings()
        self.bot_token = settings.TELEGRAM_BOT_TOKEN
        self.chat_id = settings.TELEGRAM_CHAT_ID
        self.enabled = bool(
            getattr(settings, "TELEGRAM_NOTIFICATIONS_ENABLED", False)
            and self.bot_token
            and self.chat_id
        )
        self.trading_halted = False
        self._polling_task: Optional[asyncio.Task] = None
        self._news_task: Optional[asyncio.Task] = None
        self._is_polling = False
        self._sent_news_ids = set()

    def reload_config(self):
        """Reload credentials from settings cache."""
        from shared.config import get_settings
        settings = get_settings()
        self.bot_token = settings.TELEGRAM_BOT_TOKEN
        self.chat_id = settings.TELEGRAM_CHAT_ID
        self.enabled = bool(
            getattr(settings, "TELEGRAM_NOTIFICATIONS_ENABLED", False)
            and self.bot_token
            and self.chat_id
        )
        logger.info(f"Telegram config reloaded. Enabled: {self.enabled}, Chat ID: {self.chat_id}")

    async def send_message(self, message: str, chat_id: Optional[str] = None) -> bool:
        if not self.enabled:
            logger.info(f"[Telegram Mock] {message}")
            return True

        target_chat = chat_id or self.chat_id
        if not target_chat or not self.bot_token:
            logger.warning("Telegram send failed: bot_token or chat_id missing.")
            return False

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {
            "chat_id": target_chat,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        try:
            async with httpx.AsyncClient(timeout=12.0) as client:
                resp = await client.post(url, json=payload)
                return resp.status_code == 200
        except Exception as e:
            logger.error(f"Failed to send Telegram alert: {e}")
            return False

    async def notify_signal(self, signal: Dict[str, Any]) -> bool:
        msg = (
            f"🚨 <b>YENİ SİNYAL OLUŞTU</b>\n\n"
            f"Parite: <code>{signal.get('symbol')}</code>\n"
            f"Yön: <b>{signal.get('direction', 'BUY')}</b>\n"
            f"Strateji: {signal.get('strategy', 'R10 RSI Divergence')}\n"
            f"Giriş: ${signal.get('entry_price', 0):.4f}\n"
            f"Stop-Loss: ${signal.get('stop_price', 0):.4f}\n"
            f"Take-Profit: ${signal.get('take_profit', 0):.4f}\n"
            f"Güven: %{float(signal.get('confidence', 0)) * 100:.1f}"
        )
        return await self.send_message(msg)

    async def notify_trade_open(self, pos: Dict[str, Any]) -> bool:
        entry = float(pos.get('entry_price', 0.0))
        sl = float(pos.get('stop_loss', 0.0))
        tp = float(pos.get('take_profit', 0.0))
        qty = float(pos.get('quantity', 0.0))
        volume = entry * qty

        msg = (
            f"🟢 <b>YENİ POZİSYON AÇILDI (Paper)</b>\n\n"
            f"Coin: <code>{pos.get('symbol')}</code> ({pos.get('side', 'BUY')})\n"
            f"Giriş Fiyatı: ${entry:.4f}\n"
            f"İşlem Tutarı: ${volume:.2f} ({qty} adet)\n"
            f"Stop-Loss: ${sl:.4f}\n"
            f"Take-Profit: ${tp:.4f}\n"
            f"Mod: 15m RSI Uyumsuzluğu"
        )
        return await self.send_message(msg)

    async def notify_trade_close(self, pos: Dict[str, Any], reason: str) -> bool:
        is_tp = "PROFIT" in reason or "TP" in reason
        icon = "🎯" if is_tp else "🛑"
        reason_tr = "Take-Profit (Hedef Vuruldu)" if is_tp else ("Stop-Loss (Zarar Kes)" if "STOP" in reason or "SL" in reason else reason)
        pnl = float(pos.get('realized_pnl', 0.0))
        pnl_icon = "🟢 +" if pnl >= 0 else "🔴 "

        msg = (
            f"{icon} <b>POZİSYON KAPANDI [{reason_tr}]</b>\n\n"
            f"Coin: <code>{pos.get('symbol')}</code>\n"
            f"Kapanış Fiyatı: ${float(pos.get('current_price', 0.0)):.4f}\n"
            f"Net Kâr/Zarar: <b>{pnl_icon}${pnl:.2f}</b>\n"
            f"Komisyon: ${float(pos.get('fees_paid', 0.0)):.2f}"
        )
        return await self.send_message(msg)

    async def notify_circuit_breaker(self, reason: str):
        msg = (
            f"⚠️ <b>DEVRE KESİCİ DEVREYE GİRDİ</b>\n\n"
            f"Durum: KİLİTLİ\n"
            f"Sebep: {reason}\n"
            f"Güvenlik gereği yeni alımlar durduruldu."
        )
        await self.send_message(msg)

    async def notify_bot_started(self, capital: Optional[float] = None) -> bool:
        settings = get_settings()
        cap = capital if capital is not None else getattr(settings, "INITIAL_CAPITAL", 5000.0)
        target = getattr(settings, "DAILY_TARGET_MIN", 50.0)
        tf = getattr(settings, "R10_TIMEFRAME", "15m")
        msg = (
            f"▶️ <b>KRIPTO AGENT BAŞLATILDI</b>\n\n"
            f"Mod: <b>Paper Trading (0 Risk)</b>\n"
            f"Başlangıç Sermayesi: <b>${cap:,.2f}</b>\n"
            f"Günlük Hedef: <b>${target:.2f}</b>\n"
            f"Strateji: <b>R10 RSI Uyumsuzluğu ({tf})</b>\n"
            f"Taranan Parite: <b>Binance Top 200 Spot USDT</b>\n"
            f"Piyasa taranıyor..."
        )
        return await self.send_message(msg)

    async def notify_bot_stopped(self) -> bool:
        msg = (
            f"⏹️ <b>KRIPTO AGENT DURDURULDU</b>\n\n"
            f"Piyasa taraması ve yeni alımlar duraklatıldı.\n"
            f"Mevcut açık pozisyonlar korunmaktadır."
        )
        return await self.send_message(msg)

    def _get_live_state(self) -> Dict[str, Any]:
        """Fetch current state from API state singleton."""
        settings = get_settings()
        default_cap = float(getattr(settings, "INITIAL_CAPITAL", 5000.0))
        try:
            from apps.api.app.api.state import RUNTIME_STATE, autonomous_trader
            return {
                "balance": RUNTIME_STATE.get("balance", default_cap),
                "equity": RUNTIME_STATE.get("equity", default_cap),
                "daily_pnl": RUNTIME_STATE.get("daily_pnl", 0.0),
                "open_positions": RUNTIME_STATE.get("open_positions", []),
                "closed_positions": RUNTIME_STATE.get("closed_positions", []),
                "is_active": getattr(autonomous_trader, "is_active", False),
                "circuit_state": RUNTIME_STATE.get("circuit_state", "NORMAL"),
                "last_action": RUNTIME_STATE.get("last_action", "Beklemede"),
            }
        except Exception:
            return {
                "balance": default_cap,
                "equity": default_cap,
                "daily_pnl": 0.0,
                "open_positions": [],
                "closed_positions": [],
                "is_active": not self.trading_halted,
                "circuit_state": "NORMAL",
                "last_action": "Hazır",
            }

    def handle_command(self, cmd: str, system_state: Optional[Dict[str, Any]] = None) -> str:
        cmd = cmd.strip().lower()
        settings = get_settings()
        default_cap = float(getattr(settings, "INITIAL_CAPITAL", 5000.0))
        target = float(getattr(settings, "DAILY_TARGET_MIN", 50.0))
        max_pos = int(getattr(settings, "MAX_OPEN_POSITIONS", 5))
        if system_state is None:
            system_state = self._get_live_state()

        if cmd in ("/status", "/durum"):
            is_active = (not self.trading_halted) and system_state.get("is_active", True)
            status_badge = "🟢 AKTİF (Taranıyor)" if is_active else "⏹️ DURDURULDU (Beklemede)"
            equity = float(system_state.get("equity", default_cap))
            balance = float(system_state.get("balance", default_cap))
            daily_pnl = float(system_state.get("daily_pnl", 0.0))
            pnl_icon = "🟢 +" if daily_pnl >= 0 else "🔴 "
            open_count = len(system_state.get("open_positions", []))
            circuit = system_state.get("circuit_state", "NORMAL")

            return (
                f"📊 <b>KRIPTO AGENT GENEL DURUM</b>\n\n"
                f"Bot Durumu: {status_badge}\n"
                f"Kasa Bakiyesi: ${balance:,.2f}\n"
                f"Toplam Özkaynak: ${equity:,.2f}\n"
                f"Bugünkü Kâr/Zarar: <b>{pnl_icon}${daily_pnl:.2f}</b>\n"
                f"Günlük Hedef: ${target:.2f}\n"
                f"Açık Pozisyonlar: {open_count} / {max_pos}\n"
                f"Devre Kesici: {circuit}\n"
                f"System: {'HALTED' if self.trading_halted else 'ACTIVE'}\n"
                f"Equity: ${equity:.2f}\n"
                f"Today PnL: ${daily_pnl:.2f}"
            )

        elif cmd in ("/balance", "/bakiye"):
            bal = float(system_state.get("balance", default_cap))
            eq = float(system_state.get("equity", default_cap))
            return (
                f"💰 <b>BAKİYE & ÖZKAYNAK RAPORU</b>\n\n"
                f"Balance: ${bal:.2f}\n"
                f"Equity: ${eq:.2f}\n\n"
                f"Nakit: ${bal:,.2f} | Toplam Özkaynak: ${eq:,.2f}"
            )

        elif cmd in ("/stop", "/durdur"):
            self.trading_halted = True
            try:
                from apps.api.app.api.state import autonomous_trader
                autonomous_trader.stop()
            except Exception:
                pass
            return (
                f"⛔ Trading manually HALTED. No new positions will be opened.\n\n"
                f"⏹️ <b>OTONOM ALIMLAR DURDURULDU</b>\n"
                f"Piyasa taraması duraklatıldı. Mevcut açık pozisyonlar korunur.\n"
                f"Yeniden başlatmak için /baslat yazabilirsiniz."
            )

        elif cmd in ("/resume", "/baslat"):
            self.trading_halted = False
            try:
                from apps.api.app.api.state import autonomous_trader
                autonomous_trader.start()
            except Exception:
                pass
            return (
                f"✅ Trading RESUMED.\n\n"
                f"▶️ <b>OTONOM İŞLEMLER BAŞLATILDI</b>\n"
                f"Top 200 koinlik piyasa taraması ve alımlar aktif edildi."
            )

        elif cmd in ("/positions", "/pozisyonlar"):
            positions = system_state.get("open_positions", [])
            if not positions:
                return "No open positions.\n\n💼 <b>AÇIK POZİSYONLAR</b>\n✅ Şu an açık pozisyon bulunmuyor."

            lines = ["💼 <b>AÇIK POZİSYONLAR</b>\n"]
            for p in positions:
                sym = p.get("symbol")
                side = p.get("side", "BUY")
                qty = p.get("quantity")
                entry = float(p.get("entry_price", 0.0))
                cur = float(p.get("current_price", entry))
                sl = float(p.get("stop_loss", 0.0))
                tp = float(p.get("take_profit", 0.0))
                pnl = float(p.get("unrealized_pnl", 0.0))
                pnl_pct = ((cur - entry) / entry * 100) if entry > 0 else 0.0
                p_icon = "🟢 +" if pnl >= 0 else "🔴 "

                lines.append(
                    f"🔹 <b>{sym}</b> {side} Qty: {qty}\n"
                    f"   Giriş: ${entry:.4f} | Anlık: ${cur:.4f}\n"
                    f"   Kâr/Zarar: {p_icon}${pnl:.2f} ({pnl_pct:+.2f}%)\n"
                    f"   SL: ${sl:.4f} | TP: ${tp:.4f}"
                )
            return "\n\n".join(lines)

        elif cmd in ("/trades", "/islemler", "/sonislemler"):
            closed = system_state.get("closed_positions", [])
            if not closed:
                return "📜 <b>İŞLEM GEÇMİŞİ</b>\n\nBugün henüz kapanan bir işlem bulunmuyor."

            lines = ["📜 <b>SON KAPANAN İŞLEMLER</b>\n"]
            for p in closed[-5:]:
                sym = p.get("symbol")
                pnl = float(p.get("realized_pnl", 0.0))
                icon = "🟢 +" if pnl >= 0 else "🔴 "
                reason = p.get("exit_reason", "MANUAL")
                lines.append(f"• <b>{sym}</b>: {icon}${pnl:.2f} ({reason})")
            return "\n".join(lines)

        elif cmd in ("/start", "/yardim", "/help"):
            return (
                f"🤖 <b>KRIPTO AGENT TELEGRAM YÖNETİM PANELİ</b>\n\n"
                f"Komutlar:\n"
                f"📊 <b>/durum</b> - Kasa bakiyesi, kâr/zarar ve bot durumu\n"
                f"💼 <b>/pozisyonlar</b> - Açık işlemler ve anlık kâr/zarar\n"
                f"💰 <b>/bakiye</b> - Bakiye ve özkaynak bilgisi\n"
                f"📜 <b>/islemler</b> - Kapanan son işlemler\n"
                f"📰 <b>/haberler</b> - Canlı Türkçe kripto & makro istihbaratı\n"
                f"⏹️ <b>/durdur</b> - Yeni alımları duraklat\n"
                f"▶️ <b>/baslat</b> - Taramayı ve alımları yeniden başlat\n"
                f"❓ <b>/yardim</b> - Bu yardım menüsü"
            )

        return f"Unknown command: {cmd}\nKomutları görmek için /yardim yazabilirsiniz."

    async def handle_command_async(self, cmd: str, system_state: Optional[Dict[str, Any]] = None) -> str:
        cmd_clean = cmd.strip().lower()
        if cmd_clean in ("/haberler", "/istihbarat", "/news", "/haber"):
            return "ℹ️ <b>İSTİHBARAT AKIŞI DEVRE DIŞI</b>\n\nCanlı haber/istihbarat akışı ve çeviri motoru token ve sistem kaynaklarını korumak amacıyla kapatılmıştır."

        return self.handle_command(cmd, system_state)

    async def start_polling(self):
        """Long-polling loop to listen for user commands from Telegram."""
        if not self.bot_token or not self.chat_id:
            logger.info("Telegram polling skipped: token or chat_id not set.")
            return

        self._is_polling = True
        logger.info(f"Telegram command listener started for chat_id: {self.chat_id}")

        offset = 0
        async with httpx.AsyncClient(timeout=35.0) as client:
            while self._is_polling:
                try:
                    url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates"
                    params = {"offset": offset, "timeout": 20}
                    resp = await client.get(url, params=params)

                    if resp.status_code == 200:
                        data = resp.json()
                        for update in data.get("result", []):
                            offset = update["update_id"] + 1
                            msg = update.get("message") or update.get("edited_message")
                            if not msg:
                                continue

                            sender_chat_id = str(msg.get("chat", {}).get("id"))
                            text = msg.get("text", "").strip()

                            # Only respond to the authorized owner
                            if sender_chat_id != str(self.chat_id):
                                logger.warning(f"Unauthorized Telegram access attempt from {sender_chat_id}")
                                continue

                            if text.startswith("/"):
                                response_text = await self.handle_command_async(text)
                                await self.send_message(response_text)
                    elif resp.status_code in (401, 404):
                        logger.error(f"Telegram Bot Token invalid: {resp.text}")
                        break
                    else:
                        logger.warning(f"Telegram polling status {resp.status_code}")
                        await asyncio.sleep(2)

                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.debug(f"Telegram polling notice: {e}")
                    await asyncio.sleep(2)

        logger.info("Telegram command listener stopped.")

    def stop_polling(self):
        self._is_polling = False
        if self._polling_task and not self._polling_task.done():
            self._polling_task.cancel()
            self._polling_task = None
        if self._news_task and not self._news_task.done():
            self._news_task.cancel()
            self._news_task = None


telegram_service = TelegramNotificationService()
