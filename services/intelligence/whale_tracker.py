"""
Whale Radar & Intelligence Tracker for Binance Spot.
Monitors real-time mega trades (>= $50,000), tracks institutional volume flow,
and computes net buy/sell whale sentiment.
"""

import asyncio
import json
import time
from collections import deque
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Deque, Dict, List, Optional
import aiohttp

from shared.logging import get_logger

logger = get_logger("whale-tracker", service="intelligence")

# Tracked top liquid Binance Spot symbols for whale radar
DEFAULT_WHALE_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT",
    "DOGEUSDT", "PEPEUSDT", "SUIUSDT", "NEARUSDT", "AVAXUSDT",
    "FETUSDT", "LINKUSDT", "ADAUSDT", "RENDERUSDT", "ONDOUSDT"
]

DEFAULT_WHALE_MIN_USD = 15_000.0     # $15,000 minimum for whale trade
MEGA_WHALE_MIN_USD = 75_000.0        # $75,000 for mega whale alert
SUPER_WHALE_MIN_USD = 250_000.0      # $250,000 for super whale alert


@dataclass
class WhaleTrade:
    trade_id: str
    symbol: str
    price: float
    quantity: float
    total_usd: float
    side: str              # "BUY" or "SELL"
    timestamp: str        # "HH:MM:SS"
    timestamp_ms: int
    is_mega: bool
    is_super: bool
    source: str = "Binance Spot"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class WhaleRadarTracker:
    """Real-time aggregator and WebSocket streamer for large Binance trades."""

    def __init__(
        self,
        symbols: Optional[List[str]] = None,
        min_usd: float = DEFAULT_WHALE_MIN_USD,
        max_buffer: int = 150
    ):
        self.symbols = symbols or list(DEFAULT_WHALE_SYMBOLS)
        self.min_usd = min_usd
        self.max_buffer = max_buffer
        self._trades: Deque[WhaleTrade] = deque(maxlen=max_buffer)
        self._lock = asyncio.Lock()
        self._is_running = False
        self._ws_task: Optional[asyncio.Task] = None
        self._session: Optional[aiohttp.ClientSession] = None
        self._total_whale_buys_usd = 0.0
        self._total_whale_sells_usd = 0.0

    def is_running(self) -> bool:
        return self._is_running

    def add_trade(self, trade: WhaleTrade):
        self._trades.append(trade)
        if trade.side == "BUY":
            self._total_whale_buys_usd += trade.total_usd
        else:
            self._total_whale_sells_usd += trade.total_usd

    async def get_recent_trades(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Return the most recent whale trades (newest first)."""
        async with self._lock:
            trades_list = list(self._trades)
        trades_list.reverse()
        return [t.to_dict() for t in trades_list[:limit]]

    async def get_flow_summary(self) -> Dict[str, Any]:
        """Compute aggregated net flow and symbol accumulation metrics."""
        async with self._lock:
            trades = list(self._trades)

        total_buy_usd = sum(t.total_usd for t in trades if t.side == "BUY")
        total_sell_usd = sum(t.total_usd for t in trades if t.side == "SELL")
        total_vol = total_buy_usd + total_sell_usd
        net_flow_usd = total_buy_usd - total_sell_usd
        buy_ratio_pct = round((total_buy_usd / total_vol * 100.0), 1) if total_vol > 0 else 50.0

        # Symbol breakdown
        symbol_stats: Dict[str, Dict[str, float]] = {}
        for t in trades:
            s = t.symbol.replace("USDT", "")
            if s not in symbol_stats:
                symbol_stats[s] = {"buy_usd": 0.0, "sell_usd": 0.0, "count": 0}
            if t.side == "BUY":
                symbol_stats[s]["buy_usd"] += t.total_usd
            else:
                symbol_stats[s]["sell_usd"] += t.total_usd
            symbol_stats[s]["count"] += 1

        symbol_summary = []
        for sym, stats in symbol_stats.items():
            b = stats["buy_usd"]
            s = stats["sell_usd"]
            net = b - s
            tot = b + s
            symbol_summary.append({
                "symbol": sym,
                "buy_usd": round(b, 2),
                "sell_usd": round(s, 2),
                "net_usd": round(net, 2),
                "total_usd": round(tot, 2),
                "count": stats["count"],
                "sentiment": "BOĞA (ALIM)" if net > 0 else ("AYI (SATIŞ)" if net < 0 else "NÖTR")
            })

        # Sort by total volume descending
        symbol_summary.sort(key=lambda x: x["total_usd"], reverse=True)

        sentiment_label = "GÜÇLÜ ALIM 🟢" if buy_ratio_pct >= 60.0 else (
            "GÜÇLÜ SATIŞ 🔴" if buy_ratio_pct <= 40.0 else "DENGELİ / NÖTR ⚖️"
        )

        return {
            "total_trades_tracked": len(trades),
            "total_volume_usd": round(total_vol, 2),
            "total_buy_usd": round(total_buy_usd, 2),
            "total_sell_usd": round(total_sell_usd, 2),
            "net_flow_usd": round(net_flow_usd, 2),
            "buy_ratio_pct": buy_ratio_pct,
            "sentiment": sentiment_label,
            "mega_trades_count": sum(1 for t in trades if t.is_mega),
            "symbols": symbol_summary[:8]
        }

    async def seed_historical_whales(self):
        """Fetch recent large trades from Binance REST API on startup so radar is immediately populated."""
        if not self._session:
            self._session = aiohttp.ClientSession()

        logger.info("Seeding initial whale trades from Binance REST API...")
        for sym in self.symbols[:6]:  # Seed top 6 majors
            urls = [
                f"https://data-api.binance.vision/api/v3/aggTrades?symbol={sym}&limit=80",
                f"https://api.binance.com/api/v3/aggTrades?symbol={sym}&limit=80"
            ]
            for url in urls:
                try:
                    async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=3)) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            for item in data:
                                p = float(item["p"])
                                q = float(item["q"])
                                usd_val = p * q
                                if usd_val >= self.min_usd:
                                    is_buyer_maker = bool(item["m"])
                                    side = "SELL" if is_buyer_maker else "BUY"
                                    ts_ms = int(item["T"])
                                    ts_str = datetime.fromtimestamp(ts_ms / 1000.0).strftime("%H:%M:%S")
                                    trade = WhaleTrade(
                                        trade_id=str(item["a"]),
                                        symbol=sym,
                                        price=p,
                                        quantity=q,
                                        total_usd=round(usd_val, 2),
                                        side=side,
                                        timestamp=ts_str,
                                        timestamp_ms=ts_ms,
                                        is_mega=usd_val >= MEGA_WHALE_MIN_USD,
                                        is_super=usd_val >= SUPER_WHALE_MIN_USD
                                    )
                                    self.add_trade(trade)
                            break
                except Exception:
                    continue
        logger.info(f"Seeded {len(self._trades)} whale trades into buffer.")

    async def _ws_consumer_loop(self):
        """Persistent WebSocket loop for Binance combined aggTrade streams."""
        streams = "/".join([f"{s.lower()}@aggTrade" for s in self.symbols])
        ws_url = f"wss://stream.binance.com:9443/stream?streams={streams}"

        while self._is_running:
            try:
                if not self._session or self._session.closed:
                    self._session = aiohttp.ClientSession()

                logger.info("Connecting to Binance Combined Whale Stream...")
                async with self._session.ws_connect(ws_url, heartbeat=25.0) as ws:
                    logger.info("Binance Whale Stream connected successfully.")
                    async for msg in ws:
                        if not self._is_running:
                            break
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            payload = json.loads(msg.data)
                            data = payload.get("data", {})
                            if data.get("e") == "aggTrade":
                                price = float(data["p"])
                                qty = float(data["q"])
                                total_usd = price * qty
                                if total_usd >= self.min_usd:
                                    sym = data["s"]
                                    # In Binance: m=True means maker was buyer => taker was seller => SELL
                                    is_buyer_maker = bool(data["m"])
                                    side = "SELL" if is_buyer_maker else "BUY"
                                    ts_ms = int(data["T"])
                                    ts_str = datetime.fromtimestamp(ts_ms / 1000.0).strftime("%H:%M:%S")

                                    trade = WhaleTrade(
                                        trade_id=str(data["a"]),
                                        symbol=sym,
                                        price=price,
                                        quantity=qty,
                                        total_usd=round(total_usd, 2),
                                        side=side,
                                        timestamp=ts_str,
                                        timestamp_ms=ts_ms,
                                        is_mega=total_usd >= MEGA_WHALE_MIN_USD,
                                        is_super=total_usd >= SUPER_WHALE_MIN_USD
                                    )
                                    async with self._lock:
                                        self.add_trade(trade)

                                    if trade.is_mega:
                                        icon = "💥 SUPER BALİNA" if trade.is_super else "🐋 MEGA BALİNA"
                                        logger.info(
                                            f"[{icon}] {side} {sym} | ${total_usd:,.2f} @ ${price:,.2f} "
                                            f"({qty:.2f} adet)"
                                        )
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            logger.warning(f"Binance WS closed or errored: {msg}")
                            break
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"Whale stream connection error: {e}. Reconnecting in 5s...")
                await asyncio.sleep(5)

    def start(self):
        """Start the background whale tracking task."""
        if not self._is_running:
            self._is_running = True
            self._ws_task = asyncio.create_task(self._run())
            logger.info("WhaleRadarTracker background worker started.")

    async def _run(self):
        # 1. Seed historical data
        try:
            await self.seed_historical_whales()
        except Exception as e:
            logger.warning(f"Failed to seed historical whales: {e}")

        # 2. Run real-time WebSocket
        await self._ws_consumer_loop()

    async def stop(self):
        """Gracefully stop the tracker."""
        self._is_running = False
        if self._ws_task:
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
        if self._session and not self._session.closed:
            await self._session.close()
        logger.info("WhaleRadarTracker stopped.")


# Singleton instance
whale_tracker = WhaleRadarTracker()
