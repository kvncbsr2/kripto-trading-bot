from dataclasses import dataclass
from typing import List, Tuple

from shared.config import get_settings
from shared.logging import get_logger
from shared.schemas import Candle

logger = get_logger("btc-regime-shield", service="risk")


@dataclass
class BTCHealthResult:
    is_dumping: bool
    return_15m_pct: float
    return_1h_pct: float
    reason: str


class BTCRegimeShield:
    """
    Protects altcoin positions from market-wide flash dumps driven by Bitcoin.
    Strictly causal (uses only historical/closed Binance candle data, zero lookahead).
    """

    def __init__(self, dump_threshold_pct: float = -1.5, enabled: bool = True):
        self.dump_threshold_pct = dump_threshold_pct
        self.enabled = enabled

    def evaluate_btc_health(self, btc_candles: List[Candle]) -> BTCHealthResult:
        settings = get_settings()
        is_enabled = getattr(settings, "BTC_REGIME_FILTER_ENABLED", self.enabled)
        threshold = getattr(settings, "BTC_DUMP_THRESHOLD_PCT", self.dump_threshold_pct)

        if not is_enabled:
            return BTCHealthResult(
                is_dumping=False,
                return_15m_pct=0.0,
                return_1h_pct=0.0,
                reason="BTC Kalkanı Devre Dışı",
            )

        if not btc_candles or len(btc_candles) < 5:
            return BTCHealthResult(
                is_dumping=False,
                return_15m_pct=0.0,
                return_1h_pct=0.0,
                reason="Yetersiz BTC verisi",
            )

        last_p = float(btc_candles[-1].close)
        prev_15m_p = float(btc_candles[-2].close) if len(btc_candles) >= 2 else last_p
        prev_1h_p = float(btc_candles[-5].close) if len(btc_candles) >= 5 else prev_15m_p

        ret_15m = ((last_p - prev_15m_p) / prev_15m_p) * 100.0 if prev_15m_p > 0 else 0.0
        ret_1h = ((last_p - prev_1h_p) / prev_1h_p) * 100.0 if prev_1h_p > 0 else 0.0

        if ret_15m <= threshold or ret_1h <= threshold:
            reason = f"Bitcoin sert düşüşte (15m: {ret_15m:+.2f}%, 1h: {ret_1h:+.2f}%)"
            logger.warning(f"BTC_DUMP_SHIELD ACTIVE: {reason}")
            return BTCHealthResult(
                is_dumping=True,
                return_15m_pct=ret_15m,
                return_1h_pct=ret_1h,
                reason=reason,
            )

        return BTCHealthResult(
            is_dumping=False,
            return_15m_pct=ret_15m,
            return_1h_pct=ret_1h,
            reason=f"BTC dengeli (15m: {ret_15m:+.2f}%, 1h: {ret_1h:+.2f}%)",
        )

    def can_trade_symbol(self, symbol: str, btc_health: BTCHealthResult) -> Tuple[bool, str]:
        if symbol == "BTC/USDT":
            return True, "BTC ana varlıktır."

        if btc_health.is_dumping:
            msg = f"🛡️ BTC TREND KALKANI: {symbol} alımı engellendi ({btc_health.reason})"
            return False, msg

        return True, "BTC Trend Kalkanı Onayladı"


btc_regime_shield = BTCRegimeShield()
