import pytest

from services.binance_connector.connector import BinanceConnector, ConnectionState
from services.market_data.market_data_service import MarketDataService


def test_binance_connector_initial_state():
    """Verifies connector initializes with clean state and empty subscriptions (AUDIT-01)."""
    connector = BinanceConnector(symbols=["BTC/USDT", "ETH/USDT"])
    assert connector.state == ConnectionState.DISCONNECTED
    status = connector.get_status()
    assert status["state"] == "DISCONNECTED"
    assert status["exchange"] == "binance"
    assert "BTC/USDT" in status["symbols_monitored"]


def test_market_data_service_freshness_checkers():
    """Verifies MarketDataService freshness checkers return False for empty/stale caches."""
    mds = MarketDataService(symbols=["BTC/USDT"])
    # Empty cache should report not fresh
    assert mds.is_ticker_fresh("BTC/USDT") is False
    assert mds.is_candle_fresh("BTC/USDT", "15m") is False
