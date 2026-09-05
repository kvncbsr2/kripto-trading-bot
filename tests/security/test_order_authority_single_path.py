import pytest
from services.execution.order_manager import OrderManager
from services.execution.paper_execution import PaperExecutionEngine
from services.risk_engine.risk_engine import RiskEngine
from shared.enums import SignalDirection
from shared.schemas import RiskDecision, Signal


@pytest.mark.asyncio
async def test_order_manager_single_authority_and_idempotency():
    engine = PaperExecutionEngine(initial_balance=5000.0)
    risk = RiskEngine(is_spot_mode=True)
    om = OrderManager(execution_engine=engine, risk_engine=risk)

    decision = RiskDecision(
        approved=True,
        symbol="BTC/USDT",
        direction=SignalDirection.LONG,
        entry_price=65000.0,
        stop_loss=64000.0,
        take_profit=67000.0,
        calculated_size=0.01,
        risk_amount_usd=25.0,
    )

    # 1. First execution through OrderManager
    order1, fill1, pos1 = await om.execute_risk_decision(
        decision=decision,
        strategy_name="Unit_Test_Strategy",
        signal_id="sig_test_100",
    )

    assert order1.status.value == "FILLED"
    assert fill1.price > 0
    assert pos1.status.value == "OPEN"
    assert om.verify_protective_stop_invariant(engine.open_positions)[0] is True

    # 2. Duplicate submission with same intent within time window must be rejected
    with pytest.raises(ValueError, match="DUPLICATE_ORDER_ATTEMPT"):
        await om.execute_risk_decision(
            decision=decision,
            strategy_name="Unit_Test_Strategy",
            signal_id="sig_test_100",
        )
