from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from services.execution.reconciliation import ReconciliationEngine
from shared.enums import OrderSide, OrderStatus, OrderType
from shared.schemas import Order


@pytest.mark.asyncio
async def test_reconciliation_engine_detects_clean_sync():
    engine = ReconciliationEngine()
    mock_client = MagicMock()
    mock_client.fetch_open_orders = AsyncMock(return_value=[
        {"id": "order_123", "symbol": "BTC/USDT", "side": "buy", "amount": 0.05}
    ])

    local_orders = {
        "order_123": Order(
            order_id="order_123",
            symbol="BTC/USDT",
            order_type=OrderType.LIMIT,
            side=OrderSide.BUY,
            quantity=0.05,
            price=60000.0,
            status=OrderStatus.OPEN,
            filled_quantity=0.0,
            average_fill_price=0.0,
            fee_paid=0.0,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
    }

    report = await engine.reconcile_orders_and_positions(
        local_open_positions={},
        local_orders=local_orders,
        exchange_client=mock_client,
    )

    assert report.is_synchronized is True
    assert len(report.discrepancies) == 0
    assert report.total_local_orders == 1
    assert report.total_exchange_orders == 1


@pytest.mark.asyncio
async def test_reconciliation_engine_detects_orphan_and_missing_orders():
    engine = ReconciliationEngine()
    mock_client = MagicMock()
    # Exchange has order_999 (not known locally)
    mock_client.fetch_open_orders = AsyncMock(return_value=[
        {"id": "order_999", "symbol": "ETH/USDT", "side": "sell", "amount": 1.5}
    ])

    # Local has order_111 (not found on exchange)
    local_orders = {
        "order_111": Order(
            order_id="order_111",
            symbol="BTC/USDT",
            order_type=OrderType.LIMIT,
            side=OrderSide.BUY,
            quantity=0.05,
            price=60000.0,
            status=OrderStatus.OPEN,
            filled_quantity=0.0,
            average_fill_price=0.0,
            fee_paid=0.0,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
    }

    report = await engine.reconcile_orders_and_positions(
        local_open_positions={},
        local_orders=local_orders,
        exchange_client=mock_client,
    )

    assert report.is_synchronized is False
    assert len(report.discrepancies) == 2

    types = [d.discrepancy_type for d in report.discrepancies]
    assert "ORDER_MISMATCH" in types
    assert "ORPHAN_EXCHANGE_ORDER" in types
