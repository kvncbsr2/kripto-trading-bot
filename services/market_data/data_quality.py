import math
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Optional

from shared.logging import get_logger
from shared.schemas import Candle

logger = get_logger("data-quality", service="market_data")


class QualitySeverity(str, Enum):
    OK = "OK"
    WARNING = "DATA_QUALITY_WARNING"
    LOCK = "TRADING_LOCK"


@dataclass
class QualityCheckResult:
    passed: bool
    severity: QualitySeverity
    reason: Optional[str] = None

    @property
    def valid(self) -> bool:
        """Compatibility property matching passed."""
        return self.passed


class DataQualityEngine:
    """
    Validates incoming Binance market data (candles, ticks, orderbook).
    Detects duplicate, missing, out-of-order, stale, OHLC invalid, abnormal price, or spread anomaly.
    """

    def __init__(
        self,
        max_stale_seconds: float = 180.0,
        max_single_candle_pct: float = 15.0,
        max_allowed_spread_bps: float = 100.0,
    ):
        self.max_stale_seconds = max_stale_seconds
        self.max_single_candle_pct = max_single_candle_pct
        self.max_allowed_spread_bps = max_allowed_spread_bps
        self._last_candle_by_symbol: Dict[str, Candle] = {}

    def validate_candle(
        self,
        candle: Candle,
        prev_candle: Optional[Candle] = None,
        current_time: Optional[datetime] = None,
    ) -> QualityCheckResult:
        now = current_time or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        candle_ts = candle.timestamp
        if candle_ts.tzinfo is None:
            candle_ts = candle_ts.replace(tzinfo=timezone.utc)

        # 0. Check NaN / Inf / Non-finite values
        for val_name, val in [
            ("open", candle.open),
            ("high", candle.high),
            ("low", candle.low),
            ("close", candle.close),
            ("volume", candle.volume),
        ]:
            if not isinstance(val, (int, float)) or not math.isfinite(val):
                logger.error(f"TRADING_LOCK: Non-finite/NaN value in {val_name} for {candle.symbol}")
                return QualityCheckResult(
                    passed=False,
                    severity=QualitySeverity.LOCK,
                    reason=f"NaN or Inf detected in {val_name} for {candle.symbol}",
                )

        # 1. OHLC Geometric Validity
        if (
            candle.high < candle.low
            or candle.high < candle.open
            or candle.high < candle.close
            or candle.low > candle.open
            or candle.low > candle.close
            or candle.volume < 0
        ):
            logger.error(
                f"TRADING_LOCK: Invalid OHLC geometry on {candle.symbol} "
                f"(O:{candle.open}, H:{candle.high}, L:{candle.low}, C:{candle.close}, V:{candle.volume})"
            )
            return QualityCheckResult(
                passed=False,
                severity=QualitySeverity.LOCK,
                reason=f"Corrupt OHLC candle geometry for {candle.symbol}",
            )

        # 2. Abnormal Price Spike Check (> 15% move within 1 candle)
        if candle.low > 0:
            range_pct = ((candle.high - candle.low) / candle.low) * 100.0
            if range_pct > self.max_single_candle_pct:
                logger.warning(
                    f"DATA_QUALITY_WARNING: Abnormal price spike on {candle.symbol} ({range_pct:.1f}% > {self.max_single_candle_pct}%)"
                )
                return QualityCheckResult(
                    passed=False,
                    severity=QualitySeverity.WARNING,
                    reason=f"Abnormal single-candle price spike of {range_pct:.1f}%",
                )

        # 3. Check sequence against previous candle (if exists)
        prev = prev_candle or self._last_candle_by_symbol.get(candle.symbol)
        if prev:
            prev_ts = prev.timestamp
            if prev_ts.tzinfo is None:
                prev_ts = prev_ts.replace(tzinfo=timezone.utc)

            # Duplicate candle check
            if candle_ts == prev_ts:
                return QualityCheckResult(
                    passed=False,
                    severity=QualitySeverity.WARNING,
                    reason=f"Duplicate candle timestamp {candle_ts.isoformat()}",
                )

            # Out-of-order check
            if candle_ts < prev_ts:
                logger.error(
                    f"TRADING_LOCK: Out-of-order candle received for {candle.symbol} ({candle_ts} < {prev_ts})"
                )
                return QualityCheckResult(
                    passed=False,
                    severity=QualitySeverity.LOCK,
                    reason=f"Out-of-order candle timestamp for {candle.symbol}",
                )

        # 4. Stale price check (Live stream only when current_time is now)
        if current_time is None:
            candle_age = (now - candle_ts).total_seconds()
            tf_val = getattr(candle.timeframe, "value", str(candle.timeframe))
            tf_seconds = {
                "1m": 60,
                "5m": 300,
                "15m": 900,
                "1h": 3600,
                "4h": 14400,
                "1d": 86400,
            }.get(tf_val, 0)
            stale_threshold = tf_seconds + self.max_stale_seconds
            if candle_age > stale_threshold:
                logger.warning(
                    f"DATA_QUALITY_WARNING: Stale market data for {candle.symbol} ({candle_age:.0f}s old > {stale_threshold:.0f}s)"
                )
                return QualityCheckResult(
                    passed=False,
                    severity=QualitySeverity.WARNING,
                    reason=f"Market data stale by {candle_age:.0f}s",
                )

        # All passed
        self._last_candle_by_symbol[candle.symbol] = candle
        return QualityCheckResult(passed=True, severity=QualitySeverity.OK)

    def validate_spread(self, symbol: str, bid: float, ask: float) -> QualityCheckResult:
        if bid <= 0 or ask <= 0:
            return QualityCheckResult(
                passed=False,
                severity=QualitySeverity.LOCK,
                reason=f"Invalid bid/ask prices ({bid}, {ask})",
            )

        if bid >= ask:
            logger.error(
                f"TRADING_LOCK: Inverted spread detected for {symbol} (bid {bid} >= ask {ask})"
            )
            return QualityCheckResult(
                passed=False,
                severity=QualitySeverity.LOCK,
                reason=f"Inverted spread on {symbol}",
            )

        spread_bps = ((ask - bid) / ask) * 10000.0
        if spread_bps > self.max_allowed_spread_bps:
            logger.warning(
                f"DATA_QUALITY_WARNING: Spread anomaly on {symbol} ({spread_bps:.1f} bps > {self.max_allowed_spread_bps} bps)"
            )
            return QualityCheckResult(
                passed=False,
                severity=QualitySeverity.WARNING,
                reason=f"Spread anomaly: {spread_bps:.1f} bps",
            )

        return QualityCheckResult(passed=True, severity=QualitySeverity.OK)
