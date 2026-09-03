import os
import sys
from datetime import datetime, timedelta, timezone
from typing import List

# Ensure project root is in PYTHONPATH
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")
import numpy as np

from services.feature_engine.features import FeatureEngine
from services.paper_broker.broker import PaperBroker
from services.performance_engine.journal import ExperimentJournal
from services.regime_engine.detector import RegimeDetector
from services.risk_engine.risk_engine import RiskEngine
from services.strategy_engine.strategy_manager import StrategyManager
from shared.enums import Timeframe
from shared.logging import get_logger
from shared.schemas import Candle

logger = get_logger("7day-experiment", service="experiment")


def generate_synthetic_candles(
    start_price: float = 64000.0,
    n_days: int = 7,
    candles_per_day: int = 96,  # 15m candles
) -> List[Candle]:
    """
    Generates realistic 15m crypto market candles across 7 days
    incorporating trend, range, and RSI divergence conditions.
    """
    total_candles = n_days * candles_per_day
    base_time = datetime.now(timezone.utc) - timedelta(days=n_days)

    candles: List[Candle] = []
    current_close = start_price

    rng = np.random.default_rng(seed=123)

    for i in range(total_candles):
        ts = base_time + timedelta(minutes=15 * i)

        # Regimes: Days 1-2 Bull Trend, Days 3-4 Sideways, Days 5-7 Swing Divergence
        day = (i // candles_per_day) + 1
        if day in [1, 2]:
            drift = 0.0004
            vol = 0.003
        elif day in [3, 4]:
            drift = -0.0001
            vol = 0.0025
        else:
            drift = 0.0002
            vol = 0.004

        pct_change = rng.normal(drift, vol)
        open_price = current_close
        close_price = open_price * (1.0 + pct_change)
        high_price = max(open_price, close_price) * (1.0 + abs(rng.normal(0, 0.0015)))
        low_price = min(open_price, close_price) * (1.0 - abs(rng.normal(0, 0.0015)))
        volume = float(rng.uniform(15.0, 120.0))

        current_close = close_price

        candles.append(
            Candle(
                symbol="BTC/USDT",
                timeframe=Timeframe.M15,
                timestamp=ts,
                open=round(open_price, 2),
                high=round(high_price, 2),
                low=round(low_price, 2),
                close=round(close_price, 2),
                volume=round(volume, 4),
            )
        )

    return candles


def run_7day_validation_experiment():
    print("================================================================================")
    print("🚀 STARTING KRIPTO AGENT — 7-DAY $5,000 PAPER TRADING VALIDATION EXPERIMENT")
    print("================================================================================")

    initial_capital = 5000.0
    broker = PaperBroker(
        initial_balance=initial_capital, maker_fee=0.001, taker_fee=0.001, slippage_bps=5.0
    )
    strategy_mgr = StrategyManager()
    regime_detector = RegimeDetector()
    risk_engine = RiskEngine()

    candles = generate_synthetic_candles(start_price=64000.0, n_days=7, candles_per_day=96)
    candles_per_day = 96

    daily_journals = []

    for day in range(1, 8):
        day_start_idx = (day - 1) * candles_per_day
        day_end_idx = day * candles_per_day
        day_candles = candles[day_start_idx:day_end_idx]

        day_start_equity = broker.portfolio.equity
        day_closed_positions_start = len(broker.closed_positions_history)

        print(f"\n--- [DAY {day}] --- Starting Equity: ${day_start_equity:.2f}")

        # Simulate 15m ticks within the day
        for c_idx, current_candle in enumerate(day_candles):
            global_idx = day_start_idx + c_idx
            history_slice = candles[: global_idx + 1]

            # 1. Update existing open positions with current high/low
            broker.check_position_stops_and_targets(
                symbol="BTC/USDT",
                high=current_candle.high,
                low=current_candle.low,
                close=current_candle.close,
            )

            # Need warm-up for features
            if len(history_slice) < 30:
                continue

            # 2. Extract features & regime
            feat = FeatureEngine.get_latest_feature_vector(history_slice, "BTC/USDT", Timeframe.M15)
            if not feat:
                continue

            regime = regime_detector.detect(feat)

            # 3. Evaluate strategies & signals
            signals = strategy_mgr.evaluate(feat, regime)

            for sig in signals:
                decision = risk_engine.evaluate_signal(
                    sig, broker.portfolio.get_state(), current_candle.timestamp
                )
                if decision.approved:
                    broker.execute_market_order(decision, strategy_name=sig.strategy)

        day_end_equity = broker.portfolio.equity
        day_positions = broker.closed_positions_history[day_closed_positions_start:]

        journal = ExperimentJournal.generate_daily_journal(
            day_number=day,
            starting_equity=day_start_equity,
            ending_equity=day_end_equity,
            day_positions=day_positions,
        )
        daily_journals.append(journal)

        print(f"DAY {day} SUMMARY:")
        print(f"  Ending Equity: ${day_end_equity:.2f} | Net PnL: ${journal['net_pnl']:+.2f}")
        print(
            f"  Trades: {journal['trades_count']} | Win Rate: {journal['win_rate']}% | Profit Factor: {journal['profit_factor']}"
        )
        print(
            f"  Targets Hit: $20: {'✅' if journal['target_hit_20'] else '❌'} | $50: {'✅' if journal['target_hit_50'] else '❌'} | $100: {'✅' if journal['target_hit_100'] else '❌'}"
        )
        print(f"  Risk Status: {journal['risk_status']}")

    # Final 7-Day Performance Report
    final_report = ExperimentJournal.generate_7day_final_report(
        initial_capital=initial_capital,
        final_equity=broker.portfolio.equity,
        all_closed_positions=broker.closed_positions_history,
        daily_journals=daily_journals,
    )

    print("\n================================================================================")
    print("📊 KRIPTO AGENT — 7-DAY FINAL VALIDATION REPORT")
    print("================================================================================")
    print(f"Initial Capital:        ${final_report['initial_capital']:.2f}")
    print(f"Final Equity:           ${final_report['final_equity']:.2f}")
    print(
        f"Total Net PnL:          ${final_report['total_net_pnl']:+.2f} ({final_report['total_return_pct']:+.2f}%)"
    )
    print(f"Average Daily PnL:      ${final_report['average_daily_pnl']:+.2f}")
    print(f"Total Trades:           {final_report['total_trades']}")
    print(f"Win Rate:               {final_report['win_rate']}%")
    print(f"Profit Factor:          {final_report['profit_factor']}")
    print(f"Expectancy:             ${final_report['expectancy']:.2f}")
    print(f"Max Drawdown:           {final_report['max_drawdown']:.2f}%")
    print(f"Total Fees Paid:        ${final_report['total_fees']:.2f}")
    print("Target Hits:")
    print(f"  $20/day target hit:   {final_report['target_hits']['target_20_hit_rate']}")
    print(f"  $50/day target hit:   {final_report['target_hits']['target_50_hit_rate']}")
    print(f"  $100/day target hit:  {final_report['target_hits']['target_100_hit_rate']}")
    print(f"Strategy Contributions: {final_report['strategy_contributions']}")
    print("Sensitivity Analysis:")
    print(
        f"  Fees +25% Net PnL:    ${final_report['sensitivity_analysis']['fee_plus_25pct_net_pnl']:.2f}"
    )
    print(
        f"  Slippage 2x Net PnL:  ${final_report['sensitivity_analysis']['slippage_2x_net_pnl']:.2f}"
    )
    print("Monte Carlo Results:")
    print(f"  Expected Drawdown:    {final_report['monte_carlo'].get('expected_drawdown')}%")
    print(f"  95% Drawdown:         {final_report['monte_carlo'].get('drawdown_95th')}%")
    print(f"  Worst Case Drawdown:  {final_report['monte_carlo'].get('worst_case_drawdown')}%")
    print(
        f"  Prob Streak >= 3:     {final_report['monte_carlo'].get('prob_losing_streak_3_plus')}%"
    )
    print(f"\nFINAL VERDICT: {final_report['verdict']}")
    print("================================================================================")
    return final_report


if __name__ == "__main__":
    run_7day_validation_experiment()
