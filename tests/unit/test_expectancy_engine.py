import pytest
from services.risk_engine.expectancy_engine import ExpectancyEngine
from services.risk_engine.circuit_breaker import CircuitBreaker, CircuitState


def test_expectancy_mentor_numbers_exact_match():
    closed_positions = [
        {'position_id': 'P1', 'symbol': 'BTC/USDT', 'realized_pnl': 34.96},
        {'position_id': 'P2', 'symbol': 'ETH/USDT', 'realized_pnl': -24.60},
        {'position_id': 'P3', 'symbol': 'SOL/USDT', 'realized_pnl': -2.60},
        {'position_id': 'P4', 'symbol': 'BNB/USDT', 'realized_pnl': 26.74},
    ]

    metrics = ExpectancyEngine.calculate_from_positions(closed_positions)

    assert metrics['sample_size'] == 4
    assert metrics['winning_trades'] == 2
    assert metrics['losing_trades'] == 2
    assert metrics['win_rate_pct'] == 50.0
    assert metrics['loss_rate_pct'] == 50.0
    assert metrics['average_win_usd'] == 30.85
    assert metrics['average_loss_usd'] == 13.60
    assert metrics['win_loss_ratio_b'] == 2.27
    assert metrics['breakeven_win_rate_pct'] == 30.6
    assert metrics['edge_buffer_pct'] == 19.4
    assert metrics['expectancy_usd_per_trade'] == 8.63
    assert metrics['expectancy_r'] == 0.63
    assert metrics['is_positive_expectancy'] is True
    assert metrics['should_halt'] is False
    assert metrics['status_badge'] == 'YÖNDESİNİZ'


def test_expectancy_negative_circuit_breaker_trigger():
    losing_trades = [
        {'position_id': 'P1', 'symbol': 'BTC/USDT', 'realized_pnl': 10.0},
        {'position_id': 'P2', 'symbol': 'ETH/USDT', 'realized_pnl': -20.0},
        {'position_id': 'P3', 'symbol': 'SOL/USDT', 'realized_pnl': -25.0},
        {'position_id': 'P4', 'symbol': 'BNB/USDT', 'realized_pnl': -15.0},
        {'position_id': 'P5', 'symbol': 'XRP/USDT', 'realized_pnl': -20.0},
    ]

    metrics = ExpectancyEngine.calculate_from_positions(losing_trades)

    assert metrics['sample_size'] == 5
    assert metrics['win_rate_pct'] == 20.0
    assert metrics['expectancy_usd_per_trade'] < 0.0
    assert metrics['is_positive_expectancy'] is False
    assert metrics['should_halt'] is True
    assert metrics['status'] == 'NEGATIVE_EXPECTANCY_HALTED'
    assert metrics['status_badge'] == 'DURDURMA AKTİF'

    cb = CircuitBreaker()
    tripped, reason, event_type = cb.check_expectancy(losing_trades)
    assert tripped is True
    assert cb.state == CircuitState.LOCKED
    assert 'EXPECTANCY_HALT' in reason


def test_expectancy_empty_positions():
    metrics = ExpectancyEngine.calculate_from_positions([])
    assert metrics['sample_size'] == 0
    assert metrics['should_halt'] is False
    assert metrics['status'] == 'INSUFFICIENT_DATA'
    assert metrics['status_badge'] == 'BEKLENİYOR'


def test_expectancy_trade_ledger_dicts_with_net_pnl():
    trade_ledger = [
        {'position_id': 'P1', 'symbol': 'BTC/USDT', 'net_pnl': 34.96},
        {'position_id': 'P2', 'symbol': 'ETH/USDT', 'net_pnl': -24.60},
        {'position_id': 'P3', 'symbol': 'SOL/USDT', 'net_pnl': -2.60},
        {'position_id': 'P4', 'symbol': 'BNB/USDT', 'net_pnl': 26.74},
    ]
    metrics = ExpectancyEngine.calculate_from_positions(trade_ledger)
    assert metrics['sample_size'] == 4
    assert metrics['expectancy_usd_per_trade'] == 8.63
    assert metrics['win_loss_ratio_b'] == 2.27
    assert metrics['breakeven_win_rate_pct'] == 30.6
