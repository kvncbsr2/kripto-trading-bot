import pytest

from services.execution.live_binance_execution import BinanceLiveExecutionEngine
from shared.config import get_settings


def test_live_execution_engine_rejects_when_not_armed():
    """Verifies that live execution engine refuses initialization when disarmed (AUDIT-13)."""
    settings = get_settings()

    # Even if someone attempts to pass credentials, without live_trading AND armed it must fail
    with pytest.raises(RuntimeError) as exc:
        BinanceLiveExecutionEngine(api_key="mock_key", api_secret="mock_secret", armed=False)

    assert "DISABLED" in str(exc.value) or "DISARMED" in str(exc.value) or "LOCKED" in str(exc.value)
