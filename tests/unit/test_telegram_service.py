import pytest
from unittest.mock import patch, AsyncMock
from services.notification_service.telegram_service import TelegramNotificationService


@pytest.mark.asyncio
async def test_telegram_mock_when_disabled():
    service = TelegramNotificationService()
    service.enabled = False
    
    # In mock mode, send_message should return True without making HTTP requests
    res = await service.send_message("Test message")
    assert res is True

    # Test format helpers
    assert await service.notify_bot_started(5000.0) is True
    assert await service.notify_bot_stopped() is True
    assert await service.notify_trade_open({
        "symbol": "BTC/USDT",
        "side": "LONG",
        "quantity": 0.05,
        "entry_price": 79000.0,
        "stop_loss": 78000.0,
        "take_profit": 81000.0
    }) is True
    assert await service.notify_trade_close({
        "symbol": "BTC/USDT",
        "current_price": 81000.0,
        "realized_pnl": 50.0,
        "fees_paid": 5.0
    }, reason="TAKE_PROFIT") is True


def test_telegram_command_handler():
    service = TelegramNotificationService()
    state = {"equity": 5050.0, "balance": 4800.0, "daily_pnl": 50.0, "open_positions": []}
    
    res_status = service.handle_command("/status", state)
    assert "ACTIVE" in res_status
    assert "$5050.00" in res_status

    res_stop = service.handle_command("/stop", state)
    assert "HALTED" in res_stop
    assert service.trading_halted is True

    res_resume = service.handle_command("/resume", state)
    assert "RESUMED" in res_resume
    assert service.trading_halted is False
