import pytest
from unittest.mock import AsyncMock, MagicMock
from services.execution.live_binance_execution import BinanceLiveExecutionEngine
from shared.config import get_settings
from shared.enums import SignalDirection
from shared.schemas import RiskDecision

settings = get_settings()


def test_live_execution_engine_locked_by_default():
    # settings.LIVE_TRADING is False by default
    orig_live = settings.LIVE_TRADING
    try:
        settings.LIVE_TRADING = False
        with pytest.raises(RuntimeError, match="LIVE TRADING IS LOCKED"):
            BinanceLiveExecutionEngine(api_key="key", api_secret="secret", armed=True)
    finally:
        settings.LIVE_TRADING = orig_live


def test_live_execution_engine_requires_arming():
    orig_live = settings.LIVE_TRADING
    try:
        settings.LIVE_TRADING = True
        with pytest.raises(RuntimeError, match="SECURITY VIOLATION: LIVE TRADING IS DISARMED"):
            BinanceLiveExecutionEngine(api_key="key", api_secret="secret", armed=False)
    finally:
        settings.LIVE_TRADING = orig_live


def test_live_execution_engine_requires_credentials():
    orig_live = settings.LIVE_TRADING
    orig_key = settings.BINANCE_API_KEY
    orig_secret = settings.BINANCE_API_SECRET
    try:
        settings.LIVE_TRADING = True
        settings.BINANCE_API_KEY = ""
        settings.BINANCE_API_SECRET = ""
        with pytest.raises(ValueError, match="Missing Binance API key or secret"):
            BinanceLiveExecutionEngine(api_key="", api_secret="", armed=True)
    finally:
        settings.LIVE_TRADING = orig_live
        settings.BINANCE_API_KEY = orig_key
        settings.BINANCE_API_SECRET = orig_secret


@pytest.mark.asyncio
async def test_live_execution_engine_submits_stop_loss_and_fails_closed():
    orig_live = settings.LIVE_TRADING
    try:
        settings.LIVE_TRADING = True

        mock_client = MagicMock()
        # Mock entry order fill
        mock_client.create_order = AsyncMock(
            side_effect=[
                # 1st call: Market Buy entry
                {"id": "1001", "average": 50000.0, "filled": 0.01, "fee": {"cost": 0.5}},
                # 2nd call: STOP_LOSS_LIMIT fails!
                Exception("Exchange error placing stop loss"),
                # 3rd call: Emergency Market Sell liquidation
                {"id": "1002", "average": 49950.0, "filled": 0.01},
            ]
        )

        engine = BinanceLiveExecutionEngine(
            api_key="test_key",
            api_secret="test_secret",
            armed=True,
            client=mock_client,
        )

        decision = RiskDecision(
            approved=True,
            symbol="BTC/USDT",
            direction=SignalDirection.LONG,
            calculated_size=0.01,
            entry_price=50000.0,
            stop_loss=48000.0,
            take_profit=54000.0,
            risk_score=25.0,
            risk_reward_ratio=2.0,
            reason="Approved signal",
        )

        # Invariant: If protective stop fails, it must fail closed (raise RuntimeError and liquidates)
        with pytest.raises(RuntimeError, match="FAIL-CLOSED INVARIANT"):
            await engine.submit_order(decision, strategy_name="trend")

        # Verify emergency market sell order was called
        assert mock_client.create_order.call_count == 3
        calls = mock_client.create_order.call_args_list
        assert calls[0].kwargs["side"] == "buy"
        assert calls[1].kwargs["type"] == "STOP_LOSS_LIMIT"
        assert calls[2].kwargs["side"] == "sell"
        assert calls[2].kwargs["type"] == "market"

    finally:
        settings.LIVE_TRADING = orig_live
