import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from services.autonomous_runner import AutonomousPaperTrader
from services.risk_engine.risk_engine import RiskEngine
from shared.enums import SignalDirection
from shared.schemas import PortfolioState, Signal


def test_hard_daily_target_locks_at_target_max():
    """
    Verifies that under HARD target mode, reaching daily_target_max halts entries.
    """
    engine = RiskEngine(target_mode="HARD", daily_target_max=50.0, daily_target_min=35.0)

    # 1. Below target ($30 realized) -> Not locked
    portfolio_under = PortfolioState(balance=5030, equity=5030, daily_realized_pnl=30.0, daily_pnl=30.0)
    is_locked, reason, profit = engine.is_daily_profit_locked(portfolio_under)
    assert not is_locked
    assert profit == 30.0

    # 2. Reached target ($50 realized) -> Locked!
    portfolio_at = PortfolioState(balance=5050, equity=5050, daily_realized_pnl=50.0, daily_pnl=50.0)
    is_locked, reason, profit = engine.is_daily_profit_locked(portfolio_at)
    assert is_locked
    assert "Hard daily profit target reached" in reason
    assert profit == 50.0

    # 3. Evaluate signal when locked -> Rejected
    sig = Signal(
        strategy="r10_rsi_divergence",
        symbol="BTC/USDT",
        direction=SignalDirection.LONG,
        entry_price=100.0,
        stop_price=95.0,
        take_profit=110.0,
        timestamp=datetime.now(timezone.utc),
        metadata={"score": 85.0},
    )
    decision = engine.evaluate_signal(sig, portfolio_at)
    assert not decision.approved
    assert "Hard daily profit target reached" in decision.reason


def test_soft_daily_target_tiered_filter_and_ceiling():
    """
    Verifies that under SOFT target mode:
    - Below min ($70): Normal signals pass.
    - Between min ($70) and ceiling ($100): Only exceptional signals (score >= 75) pass.
    - Above ceiling ($100): Entries are completely halted to lock daily gains.
    """
    engine = RiskEngine(target_mode="SOFT", daily_target_min=70.0, daily_target_max=100.0)

    sig_normal = Signal(
        strategy="r10_rsi_divergence",
        symbol="BTC/USDT",
        direction=SignalDirection.LONG,
        entry_price=100.0,
        stop_price=95.0,
        take_profit=110.0,
        timestamp=datetime.now(timezone.utc),
        metadata={"score": 65.0},
    )
    sig_exceptional = Signal(
        strategy="r10_rsi_divergence",
        symbol="ETH/USDT",
        direction=SignalDirection.LONG,
        entry_price=2000.0,
        stop_price=1950.0,
        take_profit=2150.0,
        timestamp=datetime.now(timezone.utc),
        metadata={"score": 80.0},
    )

    # 1. Below min ($50 realized): both can pass
    p_under = PortfolioState(balance=5050, equity=5050, daily_realized_pnl=50.0, daily_pnl=50.0)
    assert not engine.is_daily_profit_locked(p_under)[0]
    dec_norm = engine.evaluate_signal(sig_normal, p_under)
    assert dec_norm.approved

    # 2. Tier 1: Reached soft min ($75 realized): normal signal (< 75) rejected, exceptional passes
    p_mid = PortfolioState(balance=5075, equity=5075, daily_realized_pnl=75.0, daily_pnl=75.0)
    assert not engine.is_daily_profit_locked(p_mid)[0]
    dec_norm_mid = engine.evaluate_signal(sig_normal, p_mid)
    assert not dec_norm_mid.approved
    assert "Soft daily target active" in dec_norm_mid.reason

    dec_exc_mid = engine.evaluate_signal(sig_exceptional, p_mid)
    assert dec_exc_mid.approved

    # 3. Tier 2: Ceiling reached ($100 realized): ALL new entries halted to lock profit
    p_max = PortfolioState(balance=5100, equity=5100, daily_realized_pnl=100.0, daily_pnl=100.0)
    is_locked, reason, profit = engine.is_daily_profit_locked(p_max)
    assert is_locked
    assert "Daily profit ceiling reached under SOFT mode" in reason

    dec_exc_max = engine.evaluate_signal(sig_exceptional, p_max)
    assert not dec_exc_max.approved
    assert "Daily profit ceiling reached under SOFT mode" in dec_exc_max.reason


@pytest.mark.asyncio
async def test_autonomous_runner_halts_new_entries_on_daily_target_lock():
    """
    Verifies that autonomous_runner halts scanning when daily profit target is reached.
    """
    runner = AutonomousPaperTrader()
    runner.is_active = True
    runner.risk_engine.target_mode = "HARD"
    runner.risk_engine.daily_target_max = 50.0

    # Mock broker with $50 daily realized profit
    mock_broker = MagicMock()
    mock_broker.open_positions = {}
    mock_broker.closed_positions_history = []
    mock_broker.daily_realized_pnl = 50.0
    mock_broker.daily_pnl = 50.0
    mock_broker.balance = 5050.0
    mock_broker.equity = 5050.0

    mock_bus = MagicMock()
    mock_bus.broker = mock_broker
    mock_bus.runtime_state = {}
    mock_bus._log_audit = MagicMock()
    runner.command_bus = mock_bus

    mock_mds = AsyncMock()
    with patch.object(runner, "_ensure_market_data_service", return_value=mock_mds):
        result = await runner.step_cycle()
        assert result["action"] == "DAILY_TARGET_LOCKED"
        assert result["daily_realized_pnl"] == 50.0
        assert "GÜNLÜK HEDEF KİLİTLENDİ" in runner.last_action
