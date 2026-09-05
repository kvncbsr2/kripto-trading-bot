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
import requests

from services.feature_engine.features import FeatureEngine
from services.paper_broker.broker import PaperBroker
from services.performance_engine.journal import ExperimentJournal
from services.regime_engine.detector import RegimeDetector
from services.risk_engine.risk_engine import RiskEngine
from services.strategy_engine.strategy_manager import StrategyManager
from shared.config import get_settings
from shared.enums import Timeframe
from shared.logging import get_logger
from shared.schemas import Candle

logger = get_logger("7day-experiment", service="experiment")
settings = get_settings()


def fetch_real_binance_candles(
    symbol: str = "BTC/USDT",
    timeframe: str = "15m",
    limit: int = 1000,
) -> List[Candle]:
    """
    ZERO SYNTHETIC DATA MANDATE (P0-2 & P0-3):
    Fetches real closed 15m historical candles directly from Binance Public REST API.
    Fails closed if the endpoint is unreachable or returns insufficient closed candles.
    """
    binance_symbol = symbol.replace("/", "").upper()
    url = f"https://api.binance.com/api/v3/klines?symbol={binance_symbol}&interval={timeframe}&limit={limit}"
    logger.info(f"Connecting to authoritative Binance REST endpoint: {url}")
    try:
        resp = requests.get(url, timeout=20)
        resp.raise_for_status()
        raw_klines = resp.json()
    except Exception as exc:
        logger.critical(f"FATAL: Binance REST API unreachable for real candles: {exc}")
        raise RuntimeError(
            f"DATA_UNAVAILABLE: Failed to retrieve authoritative candles from Binance ({exc}). "
            f"Zero synthetic fallback permitted per P0-2 policy."
        ) from exc

    if not isinstance(raw_klines, list) or len(raw_klines) < 672:
        raise RuntimeError(
            f"DATA_UNAVAILABLE: Received {len(raw_klines) if isinstance(raw_klines, list) else 0} candles, "
            f"minimum 672 required for 7-day experiment. Zero synthetic fallback permitted."
        )

    # Exclude currently forming (unclosed) candle at the tail if present
    closed_klines = raw_klines[:-1] if len(raw_klines) > 672 else raw_klines
    candles: List[Candle] = []
    for item in closed_klines:
        open_time = int(item[0])
        ts = datetime.fromtimestamp(open_time / 1000.0, tz=timezone.utc)
        candles.append(
            Candle(
                symbol=symbol,
                timeframe=Timeframe.M15,
                timestamp=ts,
                open=float(item[1]),
                high=float(item[2]),
                low=float(item[3]),
                close=float(item[4]),
                volume=float(item[5]),
            )
        )

    logger.info(
        f"Authoritative Binance market data acquired: {len(candles)} closed 15m candles. Zero synthetic data."
    )
    return candles


def run_7day_validation_experiment():
    print("================================================================================")
    print("🚀 STARTING KRIPTO AGENT — 7-DAY $5,000 PAPER TRADING VALIDATION EXPERIMENT")
    print("   [REAL BINANCE SPOT MARKET DATA — ZERO SYNTHETIC PRICE INJECTION]")
    print("================================================================================")

    initial_capital = settings.INITIAL_CAPITAL
    broker = PaperBroker(
        initial_balance=initial_capital,
        maker_fee=settings.MAKER_FEE,
        taker_fee=settings.TAKER_FEE,
        slippage_bps=settings.SLIPPAGE_BPS,
    )
    strategy_mgr = StrategyManager()
    regime_detector = RegimeDetector()
    risk_engine = RiskEngine(
        risk_per_trade=settings.RISK_PER_TRADE,
        daily_max_loss_usd=settings.DAILY_MAX_LOSS,
        max_open_positions=settings.MAX_OPEN_POSITIONS,
        max_trades_per_day=settings.MAX_TRADES_PER_DAY,
    )

    # 7 full days of 15m candles = 7 * 96 = 672 candles
    raw_candles = fetch_real_binance_candles(symbol="BTC/USDT", timeframe="15m", limit=1000)
    # Ensure at least 672 candles available
    candles = raw_candles[-672:]
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
    print(f"Coin Contributions:     {final_report['coin_contributions']}")
    print(f"Best Strategy:          {final_report['best_strategy']}")
    print(f"Worst Strategy:         {final_report['worst_strategy']}")
    print(f"Best Coin:              {final_report['best_coin']}")
    print(f"Worst Coin:             {final_report['worst_coin']}")
    print("--------------------------------------------------------------------------------")
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
