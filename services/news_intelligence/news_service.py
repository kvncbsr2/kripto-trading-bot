import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional

from shared.logging import get_logger

logger = get_logger("news-intelligence", service="news")

# Otorite RSS kaynakları
RSS_FEEDS = [
    {
        "id": "coindesk",
        "name": "CoinDesk",
        "url": "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "default_cat": "kripto",
        "badge_color": "blue"
    },
    {
        "id": "cointelegraph",
        "name": "Cointelegraph",
        "url": "https://cointelegraph.com/rss",
        "default_cat": "kripto",
        "badge_color": "amber"
    },
    {
        "id": "decrypt",
        "name": "Decrypt",
        "url": "https://decrypt.co/feed",
        "default_cat": "kripto",
        "badge_color": "emerald"
    },
    {
        "id": "theblock",
        "name": "The Block",
        "url": "https://www.theblock.co/rss.xml",
        "default_cat": "kripto",
        "badge_color": "purple"
    },
    {
        "id": "vitalik",
        "name": "Vitalik Buterin Blog",
        "url": "https://vitalik.eth.limo/feed.xml",
        "default_cat": "onchain",
        "badge_color": "indigo"
    },
    {
        "id": "google_crypto_macro",
        "name": "Global Wire (Bloomberg/Reuters/WSJ)",
        "url": "https://news.google.com/rss/search?q=crypto+OR+bitcoin+OR+fed+OR+%22interest+rate%22+OR+nvidia&hl=en-US&gl=US&ceid=US:en",
        "default_cat": "wire",
        "badge_color": "red"
    },
]

# Anahtar Kelime Haritaları
CATEGORY_KEYWORDS = {
    "wire": ["breaking", "urgent", "just in", "flash", "halts", "halted", "emergency", "alert"],
    "onchain": ["whale", "wallet", "transfer", "vitalik", "ethereum", "burn", "mint", "tvl", "uniswap", "layer 2", "solana", "l2", "on-chain", "gas fee"],
    "macro": ["fed", "federal reserve", "powell", "inflation", "cpi", "rate cut", "interest rate", "treasury", "liquidity", "recession", "gdp", "dollar", "bonds", "yield", "central bank", "m2"],
    "ai": ["ai", "artificial intelligence", "nvidia", "openai", "chip", "semiconductor", "tsmc", "gpu", "model", "superintelligence", "anthropic", "deepseek", "llm"],
    "regulation": ["sec", "cftc", "lawsuit", "sues", "judge", "court", "ruling", "senate", "congress", "mica", "doj", "biden", "trump", "gensler", "subpoena", "wells notice", "etf approval", "regulatory"],
}

BULLISH_KEYWORDS = [
    "surge", "surges", "rallies", "rally", "gain", "gains", "breakout", "approval", "approved",
    "etf", "record high", "all-time high", "ath", "inflow", "inflows", "bull", "bullish",
    "accumulat", "soars", "soar", "jump", "jumps", "milestone", "partnership", "optimism",
    "adoption", "upgrade", "outperform", "cuts rate", "rate cut"
]

BEARISH_KEYWORDS = [
    "crash", "crashes", "plunge", "plunges", "drop", "drops", "hack", "hacked", "exploit",
    "exploited", "sec sue", "sues", "lawsuit", "probe", "investigation", "outflow", "outflows",
    "bear", "bearish", "scam", "fraud", "liquidat", "insolven", "suspend", "suspends",
    "warns", "warning", "ban", "crackdown", "fine", "fined", "bankrupt", "freeze", "freezes"
]

URGENT_KEYWORDS = [
    "breaking", "urgent", "exploit", "hack", "sec charges", "emergency", "halted",
    "suspended", "indicted", "arrested", "insolvency", "chapter 11"
]


class NewsService:
    def __init__(self):
        self._cache: List[Dict] = []
        self._last_fetch_time: float = 0.0
        self._cache_ttl_seconds: float = 45.0
        self._executor = ThreadPoolExecutor(max_workers=6)
        self._lock = asyncio.Lock()

    def _determine_category(self, title: str, default_cat: str) -> str:
        t = title.lower()
        for cat, keywords in CATEGORY_KEYWORDS.items():
            if any(k in t for k in keywords):
                return cat
        return default_cat

    def _determine_sentiment(self, title: str) -> Dict[str, str]:
        t = title.lower()
        bull_hits = [k for k in BULLISH_KEYWORDS if k in t]
        bear_hits = [k for k in BEARISH_KEYWORDS if k in t]

        if len(bear_hits) > len(bull_hits):
            return {
                "label": "bearish",
                "tag": "Risk / Ayı",
                "color": "red"
            }
        elif len(bull_hits) > len(bear_hits):
            return {
                "label": "bullish",
                "tag": "Fırsat / Boğa",
                "color": "green"
            }
        else:
            return {
                "label": "neutral",
                "tag": "Nötr",
                "color": "blue"
            }

    def _check_urgency(self, title: str) -> bool:
        t = title.lower()
        return any(k in t for k in URGENT_KEYWORDS)

    def _format_time_ago(self, pub_dt: Optional[datetime]) -> str:
        if not pub_dt:
            return "Az önce"
        now = datetime.now(timezone.utc)
        if pub_dt.tzinfo is None:
            pub_dt = pub_dt.replace(tzinfo=timezone.utc)
        diff = (now - pub_dt).total_seconds()
        if diff < 60:
            return f"{max(5, int(diff))} sn önce"
        elif diff < 3600:
            return f"{int(diff // 60)} dk önce"
        elif diff < 86400:
            return f"{int(diff // 3600)} sa önce"
        else:
            return f"{int(diff // 86400)} gün önce"

    def _fetch_single_feed(self, feed_cfg: Dict) -> List[Dict]:
        items_out = []
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        try:
            req = urllib.request.Request(feed_cfg["url"], headers=headers)
            with urllib.request.urlopen(req, timeout=3.5) as resp:
                content = resp.read()
                try:
                    root = ET.fromstring(content)
                except Exception:
                    # Clean any non-xml artifacts
                    cleaned = re.sub(r'[\x00-\x08\x0B\x0C\x0E-\x1F]', '', content.decode('utf-8', errors='ignore'))
                    root = ET.fromstring(cleaned)

                # Standart RSS <item>
                elements = root.findall('.//item')
                is_atom = False
                if not elements:
                    # Atom <entry>
                    elements = root.findall('.//{http://www.w3.org/2005/Atom}entry')
                    is_atom = True

                for el in elements[:20]:
                    title_el = el.find('title') if not is_atom else el.find('{http://www.w3.org/2005/Atom}title')
                    title = title_el.text.strip() if title_el is not None and title_el.text else ""
                    if not title:
                        continue

                    # Clean html tags from title
                    title = re.sub(r'<[^>]+>', '', title)

                    link = ""
                    if is_atom:
                        link_el = el.find('{http://www.w3.org/2005/Atom}link')
                        if link_el is not None:
                            link = link_el.attrib.get('href', '')
                    else:
                        link_el = el.find('link')
                        if link_el is not None and link_el.text:
                            link = link_el.text.strip()

                    pub_date_str = ""
                    pub_dt = None
                    if is_atom:
                        up_el = el.find('{http://www.w3.org/2005/Atom}updated')
                        if up_el is not None and up_el.text:
                            pub_date_str = up_el.text.strip()
                    else:
                        date_el = el.find('pubDate')
                        if date_el is not None and date_el.text:
                            pub_date_str = date_el.text.strip()

                    if pub_date_str:
                        try:
                            pub_dt = parsedate_to_datetime(pub_date_str)
                        except Exception:
                            try:
                                pub_dt = datetime.fromisoformat(pub_date_str.replace("Z", "+00:00"))
                            except Exception:
                                pub_dt = None

                    category = self._determine_category(title, feed_cfg["default_cat"])
                    sentiment = self._determine_sentiment(title)
                    is_urgent = self._check_urgency(title)
                    time_ago = self._format_time_ago(pub_dt)
                    ts = pub_dt.timestamp() if pub_dt else time.time()

                    items_out.append({
                        "id": f"{feed_cfg['id']}-{hash(title)}",
                        "source": feed_cfg["name"],
                        "source_badge_color": feed_cfg["badge_color"],
                        "title": title,
                        "link": link,
                        "category": category,
                        "sentiment": sentiment,
                        "is_urgent": is_urgent,
                        "time_ago": time_ago,
                        "timestamp": ts,
                    })
        except Exception as e:
            logger.debug(f"Feed fetch non-fatal notice for {feed_cfg['name']}: {e}")
        return items_out

    async def get_live_news(self, force_refresh: bool = False, category_filter: Optional[str] = None) -> Dict:
        async with self._lock:
            now = time.time()
            if force_refresh or (now - self._last_fetch_time > self._cache_ttl_seconds) or not self._cache:
                loop = asyncio.get_running_loop()
                all_results = []
                tasks = [
                    loop.run_in_executor(self._executor, self._fetch_single_feed, feed_cfg)
                    for feed_cfg in RSS_FEEDS
                ]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for res in results:
                    if isinstance(res, list):
                        all_results.extend(res)

                # Başlık benzerliğine göre tekrarları temizle (Deduplicate)
                seen_titles = set()
                unique_items = []
                for item in sorted(all_results, key=lambda x: x["timestamp"], reverse=True):
                    # Temel kelime köklerini al
                    norm = re.sub(r'[^a-zA-Z0-9]', '', item["title"][:40].lower())
                    if norm not in seen_titles:
                        seen_titles.add(norm)
                        unique_items.append(item)

                if unique_items:
                    self._cache = unique_items[:120]  # En güncel 120 haber
                    self._last_fetch_time = now

        items = self._cache
        if category_filter and category_filter != "all":
            items = [i for i in items if i["category"] == category_filter]

        return {
            "status": "success",
            "count": len(items),
            "total_available": len(self._cache),
            "last_updated": datetime.fromtimestamp(self._last_fetch_time, timezone.utc).isoformat() if self._last_fetch_time else None,
            "items": items
        }

    def _sync_translate(self, text: str) -> str:
        if not text:
            return ""
        import json
        import urllib.parse
        import urllib.request
        try:
            url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=en&tl=tr&dt=t&q={urllib.parse.quote(text)}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            with urllib.request.urlopen(req, timeout=4.0) as r:
                data = json.loads(r.read().decode("utf-8"))
                res = "".join([p[0] for p in data[0] if p and p[0]])
                return res if res else text
        except Exception:
            return text

    async def translate_to_turkish(self, text: str) -> str:
        """Translate headline text to Turkish using public fast translation."""
        if not text:
            return ""
        return await asyncio.to_thread(self._sync_translate, text)

    async def get_latest_translated(self, count: int = 5) -> List[Dict]:
        """Fetch top headlines with automatic Turkish translation."""
        feed = await self.get_live_news()
        items = feed.get("items", [])[:count]
        tasks = [self.translate_to_turkish(it["title"]) for it in items]
        translations = await asyncio.gather(*tasks, return_exceptions=True)
        for i, trans in enumerate(translations):
            if isinstance(trans, str) and trans:
                items[i]["title_tr"] = trans
            else:
                items[i]["title_tr"] = items[i]["title"]
        return items


# Singleton instance
news_service = NewsService()
