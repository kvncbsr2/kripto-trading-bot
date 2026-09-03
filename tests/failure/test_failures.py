from datetime import datetime, timedelta, timezone

import pytest

from services.execution.live_binance_execution import BinanceLiveExecutionEngine
from services.market_data.data_quality import DataQualityEngine, QualitySeverity
from services.market_scanner.scanner import BinanceMarketScanner
from services.risk_engine.circuit_breaker import CircuitBreaker, CircuitState
from shared.enums import Timeframe
from shared.schemas import Candle, PortfolioState


def test_live_trading_hard_lock_security_exception():
    """Verify that initializing BinanceLiveExecutionEngine throws a hard RuntimeError."""
    with pytest.raises(RuntimeError, match="LIVE TRADING IS LOCKED"):
        BinanceLiveExecutionEngine()


def test_data_quality_corrupted_ohlc_lock():
    """Verify corrupted candle geometry triggers TRADING_LOCK."""
    dq = DataQualityEngine()
    corrupt_candle = Candle(
        symbol="BTC/USDT",
        timeframe=Timeframe.M15,
        timestamp=datetime.now(timezone.utc),
        open=60000.0,
        high=59000.0,  # High < Open / Low! Corrupt
        low=61000.0,
        close=60500.0,
        volume=10.0,
    )
    res = dq.validate_candle(corrupt_candle)
    assert res.passed is False
    assert res.severity == QualitySeverity.LOCK


def test_data_quality_out_of_order_candle():
    """Verify out-of-order candle triggers TRADING_LOCK."""
    dq = DataQualityEngine()
    t1 = datetime.now(timezone.utc)
    t2 = t1 - timedelta(minutes=15)  # backward in time

    c1 = Candle(
        symbol="BTC/USDT",
        timeframe=Timeframe.M15,
        timestamp=t1,
        open=60000.0,
        high=60100.0,
        low=59900.0,
        close=60050.0,
        volume=10.0,
    )
    c2 = Candle(
        symbol="BTC/USDT",
        timeframe=Timeframe.M15,
        timestamp=t2,
        open=60050.0,
        high=60150.0,
        low=59950.0,
        close=60100.0,
        volume=10.0,
    )
    res1 = dq.validate_candle(c1)
    assert res1.passed is True

    res2 = dq.validate_candle(c2)
    assert res2.passed is False
    assert res2.severity == QualitySeverity.LOCK
    assert "Out-of-order" in (res2.reason or "")


def test_data_quality_inverted_spread_lock():
    """Verify inverted or zero spread triggers TRADING_LOCK."""
    dq = DataQualityEngine()
    res = dq.validate_spread("BTC/USDT", bid=60100.0, ask=60000.0)
    assert res.passed is False
    assert res.severity == QualitySeverity.LOCK


def test_scanner_insufficient_liquidity_rejection():
    """Verify market scanner rejects low volume coins."""
    scanner = BinanceMarketScanner(min_24h_volume=10000000.0)
    # Low volume coin ($500k 24h volume)
    res = scanner.scan_symbol_metrics(
        symbol="LOWLIQ/USDT",
        price=1.5,
        volume_24h=500000.0,
        bid=1.499,
        ask=1.501,
    )
    assert res.trade_allowed is False
    assert "INSUFFICIENT_LIQUIDITY" in (res.rejection_reason or "")


def test_scanner_excessive_spread_rejection():
    """Verify market scanner rejects coins with spread > max_spread_bps."""
    scanner = BinanceMarketScanner(max_spread_bps=15.0)
    # 50 bps spread (1.50 to 1.5075)
    res = scanner.scan_symbol_metrics(
        symbol="WIDESPREAD/USDT",
        price=1.50,
        volume_24h=50000000.0,
        bid=1.50,
        ask=1.5075,
    )
    assert res.trade_allowed is False
    assert "SPREAD_TOO_HIGH" in (res.rejection_reason or "")


def test_circuit_breaker_daily_loss_limit_transition():
    """Verify breaker transitions from NORMAL to LOCKED when loss breaches $50."""
    cb = CircuitBreaker(daily_max_loss_usd=50.0)
    assert cb.state == CircuitState.NORMAL

    # Breached daily loss
    p = PortfolioState(balance=4945.0, equity=4945.0, daily_pnl=-55.0)
    tripped, reason, _ = cb.check(p)
    assert tripped is True
    assert cb.state == CircuitState.LOCKED
    assert "DAILY_RISK_LOCK" in (reason or "")
