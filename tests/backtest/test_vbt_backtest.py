import pandas as pd

from services.backtest_engine.vbt_backtest import VectorBTBacktester


def test_vectorbt_backtest_execution():
    # Construct synthetic price series
    prices = [100.0, 102.0, 105.0, 104.0, 108.0, 110.0, 107.0, 105.0, 103.0, 106.0]
    close = pd.Series(prices)

    # Buy at bar 1, exit at bar 5 (profitable trade: 102 -> 110)
    entries = pd.Series([False, True, False, False, False, False, False, False, False, False])
    exits = pd.Series([False, False, False, False, False, True, False, False, False, False])

    vbt_runner = VectorBTBacktester(initial_capital=5000.0, fees=0.001, slippage_bps=5.0)
    result = vbt_runner.run_backtest_from_signals(close=close, entries=entries, exits=exits)

    assert result["engine"] == "vectorbt"
    assert result["initial_capital"] == 5000.0
    assert result["total_trades"] == 1
    assert result["win_rate"] == 100.0
    # Gross gain is positive, net should be positive after fees
    assert result["total_net_pnl"] > 0
    assert result["final_equity"] > 5000.0
    assert result["fees_modeled"] == 0.001


def test_vectorbt_backtest_loss_execution():
    # Buy at bar 1 (105), exit at bar 3 (98) (losing trade)
    prices = [100.0, 105.0, 102.0, 98.0, 95.0]
    close = pd.Series(prices)

    entries = pd.Series([False, True, False, False, False])
    exits = pd.Series([False, False, False, True, False])

    vbt_runner = VectorBTBacktester(initial_capital=5000.0, fees=0.001, slippage_bps=5.0)
    result = vbt_runner.run_backtest_from_signals(close=close, entries=entries, exits=exits)

    assert result["total_trades"] == 1
    assert result["win_rate"] == 0.0
    assert result["total_net_pnl"] < 0
    assert result["final_equity"] < 5000.0
