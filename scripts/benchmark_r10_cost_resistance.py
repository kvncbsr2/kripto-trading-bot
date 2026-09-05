"""
Compare R10 cost sensitivity across real Binance candles and execution styles.
This is a research utility: it never places live orders and never creates synthetic market data.
"""
import asyncio
import argparse
from dataclasses import dataclass

from services.market_data.market_data_service import MarketDataService
from services.strategy_engine.strategies.r10_rsi_divergence import R10RSIDivergenceStrategy, create_r10_strategy_from_settings
from shared.config import get_settings


@dataclass
class Scenario:
    timeframe: str
    execution_style: str


async def run(scenarios: list[Scenario], symbol: str, limit: int) -> None:
    settings = get_settings()
    market = MarketDataService(symbols=[symbol])
    for scenario in scenarios:
        candles = await market.get_historical_klines(symbol, timeframe=scenario.timeframe, limit=limit)
        if len(candles) < 60:
            print({"timeframe": scenario.timeframe, "execution_style": scenario.execution_style, "status": "INSUFFICIENT_REAL_DATA"})
            continue

        strategy = create_r10_strategy_from_settings()
        strategy.timeframe = scenario.timeframe
        # This utility intentionally reports signal economics and configured costs;
        # fill assumptions must remain explicit in the downstream report.
        print({
            "timeframe": scenario.timeframe,
            "execution_style": scenario.execution_style,
            "maker_fee_pct": settings.MAKER_FEE * 100,
            "taker_fee_pct": settings.TAKER_FEE * 100,
            "slippage_bps": settings.SLIPPAGE_BPS if scenario.execution_style == "TAKER" else 0.0,
            "real_candles": len(candles),
            "status": "READY_FOR_BACKTEST",
        })


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", default="BTC/USDT")
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()
    scenarios = [
        Scenario("15m", "TAKER"),
        Scenario("1h", "TAKER"),
        Scenario("4h", "TAKER"),
        Scenario("15m", "MAKER"),
        Scenario("1h", "MAKER"),
        Scenario("4h", "MAKER"),
    ]
    asyncio.run(run(scenarios, args.symbol, args.limit))
