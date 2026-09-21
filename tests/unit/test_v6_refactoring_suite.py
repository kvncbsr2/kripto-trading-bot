import asyncio
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pandas as pd
import pytest
from httpx import ASGITransport, AsyncClient

from apps.api.app.main import app, RUNTIME_STATE
from services.autonomous_runner import AutonomousPaperTrader
from services.execution.order_manager import OrderManager
from services.market_data.market_data_service import MarketDataService
from services.risk_engine.risk_engine import RiskEngine
from services.strategy_engine.strategies.r10_rsi_divergence import R10RSIDivergenceStrategy
from shared.enums import SignalDirection, OrderStatus, OrderType, PositionSide, PositionStatus
from shared.schemas import Candle, Signal, Timeframe, PortfolioState, Position


# =============================================================================
# 1. apps/api/app/main.py: Security & Fail-Safe State Machine
# =============================================================================
@pytest.mark.asyncio
async def test_dashboard_does_not_inject_unauthorized_admin_cookie():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/dashboard")
        assert res.status_code == 200
        cookie_header = res.headers.get("set-cookie", "")
        assert "kripto_admin_token=" not in cookie_header


@pytest.mark.asyncio
async def test_failsafe_startup_prevents_autonomous_trading_on_failure():
    with patch("apps.api.app.main.init_models_async", side_effect=RuntimeError("DB Connection Failed")), \
         patch("apps.api.app.main.market_data_service") as mock_mds, \
         patch("apps.api.app.main.autonomous_trader") as mock_trader:
        
        mock_mds._is_started = False
        mock_mds.start = MagicMock()
        mock_trader.start = MagicMock()
        mock_trader.is_active = False

        from apps.api.app.main import lifespan
        async with lifespan(app):
            assert RUNTIME_STATE["system_state"] in ("DEGRADED", "HALTED")
            mock_trader.start.assert_not_called()


# =============================================================================
# 2. services/autonomous_runner.py: Parallel Checks & Sleep Sync
# =============================================================================
def test_bar_close_sleep_calculation():
    trader = AutonomousPaperTrader()
    sleep_sec = trader._calculate_sleep_until_next_bar("15m")
    assert 0.0 <= sleep_sec <= 905.0


@pytest.mark.asyncio
async def test_step_cycle_parallel_position_checking():
    mock_mds = MagicMock()
    mock_broker = MagicMock()
    mock_broker.daily_pnl = 0.0
    mock_broker.daily_realized_pnl = 0.0
    mock_broker.unrealized_pnl = 0.0
    mock_broker.check_position_stops_and_targets.return_value = None
    mock_bus = MagicMock()
    mock_bus.broker = mock_broker
    mock_bus.runtime_state = {"is_halted": False, "system_state": "READY"}

    trader = AutonomousPaperTrader(command_bus_instance=mock_bus, market_data_service=mock_mds)
    
    pos1 = Position(
        position_id="P1",
        symbol="BTC/USDT",
        side=PositionSide.LONG,
        entry_price=60000.0,
        current_price=60100.0,
        quantity=0.1,
        stop_loss=59000.0,
        take_profit=62000.0,
        trailing_stop=59500.0,
        opened_at=datetime.now(timezone.utc),
    )
    pos2 = Position(
        position_id="P2",
        symbol="ETH/USDT",
        side=PositionSide.LONG,
        entry_price=3000.0,
        current_price=3050.0,
        quantity=1.0,
        stop_loss=2900.0,
        take_profit=3200.0,
        trailing_stop=2950.0,
        opened_at=datetime.now(timezone.utc),
    )
    mock_broker.open_positions = {"BTC/USDT": pos1, "ETH/USDT": pos2}

    async def mock_get_live_ticker(symbol):
        if "BTC" in symbol:
            return {"symbol": symbol, "price": 60200.0}
        return {"symbol": symbol, "price": 3060.0}

    trader.market_data_service.get_live_ticker = AsyncMock(side_effect=mock_get_live_ticker)
    trader.market_data_service.get_historical_klines = AsyncMock(return_value=[])
    trader._scan_single_symbol = AsyncMock(return_value=[])

    await trader.step_cycle()
    assert trader.market_data_service.get_live_ticker.call_count >= 2


# =============================================================================
# 3. services/strategy_engine/strategies/r10_rsi_divergence.py
# =============================================================================
def test_r10_max_entry_risk_pct_rejection():
    strat = R10RSIDivergenceStrategy(max_entry_risk_pct=0.04)
    base_time = datetime.now(timezone.utc) - timedelta(hours=20)
    timestamps = [base_time + timedelta(minutes=15 * i) for i in range(50)]
    closes = [100.0] * 50
    highs = [101.0] * 50
    lows = [99.0] * 50
    
    lows[20] = 90.0
    lows[40] = 92.0
    closes[49] = 100.0
    
    df = pd.DataFrame({
        "timestamp": timestamps,
        "open": closes,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": [1000.0] * 50,
        "rsi": [40.0] * 50,
        "atr": [2.0] * 50,
        "ema_9": [98.0] * 50,
    })
    df.loc[20, "rsi"] = 25.0
    df.loc[40, "rsi"] = 32.0

    signal = strat.evaluate_from_dataframe(df, "BTC/USDT")
    assert signal is None


def test_r10_intermediate_bar_violation_check():
    strat = R10RSIDivergenceStrategy(max_entry_risk_pct=0.20)
    base_time = datetime.now(timezone.utc) - timedelta(hours=20)
    timestamps = [base_time + timedelta(minutes=15 * i) for i in range(50)]
    closes = [100.0] * 50
    highs = [101.0] * 50
    lows = [99.0] * 50
    rsis = [40.0] * 50

    lows[20] = 90.0
    rsis[20] = 25.0
    lows[44] = 92.0
    rsis[44] = 30.0

    # Intermediate bar (idx 30) violates by dropping lower than P2 (low = 88 < 92)
    lows[30] = 88.0

    df = pd.DataFrame({
        "timestamp": timestamps,
        "open": closes,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": [1000.0] * 50,
        "rsi": rsis,
        "atr": [1.0] * 50,
        "ema_9": [98.0] * 50,
    })
    signal = strat.evaluate_from_dataframe(df, "BTC/USDT")
    assert signal is None


# =============================================================================
# 4. services/risk_engine/risk_engine.py: Scores, MIN_NOTIONAL & Net Equity
# =============================================================================
def test_risk_engine_min_notional_guard():
    engine = RiskEngine()
    portfolio = PortfolioState(balance=5000, equity=5000)
    sig = Signal(
        symbol="DOGE/USDT",
        strategy="audit",
        direction=SignalDirection.LONG,
        entry_price=0.10,
        stop_price=0.099,
        take_profit=0.105,
    )
    with patch("services.risk_engine.risk_engine.calculate_atr_position_size", return_value=(50.0, 0.5)):
        decision = engine.evaluate_signal(sig, portfolio)
        assert not decision.approved
        assert "REJECTED_BELOW_MIN_NOTIONAL" in decision.reason


def test_risk_engine_soft_target_signal_score_key_matching():
    engine = RiskEngine(target_mode="SOFT", daily_target_min=20.0, daily_target_max=100.0)
    portfolio = PortfolioState(balance=5000, equity=5000, daily_realized_pnl=25.0, unrealized_pnl=0.0)
    
    sig = Signal(
        symbol="BTC/USDT",
        strategy="audit",
        direction=SignalDirection.LONG,
        entry_price=100.0,
        stop_price=98.0,
        take_profit=106.0,
        metadata={"signal_score": 85.0},
    )
    decision = engine.evaluate_signal(sig, portfolio)
    assert decision.approved


# =============================================================================
# 5. services/execution/order_manager.py: Real Stops, Pruning & Idempotency
# =============================================================================
@pytest.mark.asyncio
async def test_order_manager_real_stop_dispatch():
    om = OrderManager()
    mock_engine = MagicMock()
    mock_engine.submit_stop_order = AsyncMock(return_value={"id": "real-stop-123", "status": "OPEN"})
    om.execution_engine = mock_engine

    pos = Position(
        position_id="test-pos-1",
        symbol="BTC/USDT",
        side=PositionSide.LONG,
        entry_price=60000.0,
        current_price=60000.0,
        quantity=0.5,
        stop_loss=58000.0,
        take_profit=64000.0,
        status=PositionStatus.OPEN,
    )

    stop_order = await om._ensure_protective_stop(pos)
    assert stop_order is not None
    assert stop_order.status == OrderStatus.SUBMITTED
    mock_engine.submit_stop_order.assert_called_once()
    assert "BTC/USDT" in om.protective_stops

    removed = om.remove_protective_stop("BTC/USDT")
    assert removed is not None
    assert "BTC/USDT" not in om.protective_stops


def test_order_manager_prune_stale_cache():
    om = OrderManager()
    om.orders["old-order"] = MagicMock(status=OrderStatus.FILLED, created_at=datetime.now(timezone.utc) - timedelta(hours=25))
    om.idempotency_map["old-key"] = "old-order"
    om.idempotency_timestamps["old-key"] = datetime.now(timezone.utc) - timedelta(hours=2)
    
    fresh_time = datetime.now(timezone.utc) - timedelta(minutes=5)
    om.orders["fresh-order"] = MagicMock(status=OrderStatus.FILLED, created_at=fresh_time)
    om.idempotency_map["fresh-key"] = "fresh-order"
    om.idempotency_timestamps["fresh-key"] = fresh_time

    pruned_orders, pruned_keys = om.prune_stale_cache(max_order_age_hours=24.0, max_idempotency_age_hours=1.0)
    assert pruned_orders == 1
    assert pruned_keys == 1
    assert "old-order" not in om.orders
    assert "fresh-order" in om.orders
    assert "old-key" not in om.idempotency_map
    assert "fresh-key" in om.idempotency_map


# =============================================================================
# 6. services/market_data/market_data_service.py: Freshness, Copy & Ranking
# =============================================================================
def test_market_data_service_startup_failsafe():
    mds = MarketDataService()
    assert mds.max_ticker_age_seconds == 3.0
    with patch("asyncio.get_running_loop", side_effect=RuntimeError("No loop")):
        mds.start()
        assert mds._is_started is False
        assert mds._ws_task is None


@pytest.mark.asyncio
async def test_get_all_binance_tickers_dynamic_volume_ranking():
    mds = MarketDataService()
    mds._market_caps = {
        "BTC": {"name": "Bitcoin", "rank": 1, "market_cap": 1200000000000.0},
        "ETH": {"name": "Ethereum", "rank": 2, "market_cap": 400000000000.0},
    }
    mock_tickers = {
        "BTC/USDT": {"bid": 60000.0, "ask": 60001.0, "last": 60000.5, "quoteVolume": 50000000.0},
        "ETH/USDT": {"bid": 3000.0, "ask": 3000.5, "last": 3000.25, "quoteVolume": 30000000.0},
        "UNLISTED_LOW/USDT": {"bid": 1.0, "ask": 1.01, "last": 1.005, "quoteVolume": 10000.0},
        "UNLISTED_HIGH/USDT": {"bid": 2.0, "ask": 2.01, "last": 2.005, "quoteVolume": 9999999.0},
    }
    mds.connector.rest_client.fetch_tickers = AsyncMock(return_value=mock_tickers)
    
    tickers = await mds.get_all_binance_tickers()
    assert len(tickers) == 4
    assert tickers[0]["symbol"] == "BTC/USDT"
    assert tickers[1]["symbol"] == "ETH/USDT"
    assert tickers[2]["symbol"] == "UNLISTED_HIGH/USDT"
    assert tickers[3]["symbol"] == "UNLISTED_LOW/USDT"
    assert tickers[2]["rank"] < tickers[3]["rank"]
