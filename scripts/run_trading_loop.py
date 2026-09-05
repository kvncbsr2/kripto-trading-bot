import asyncio
import signal
import sys

from database.init_db import init_models_async
from services.autonomous_runner import AutonomousPaperTrader
from services.command_bus.command_bus import CommandBus
from services.market_data.market_data_service import MarketDataService
from services.paper_broker.broker import PaperBroker
from services.risk_engine.risk_engine import RiskEngine
from shared.config import get_settings
from shared.logging import get_logger

logger = get_logger("trading-worker", service="worker")
settings = get_settings()


async def main():
    logger.info("Starting KRIPTO AGENT Trading Worker...")
    await init_models_async()

    runtime_state = {
        "balance": settings.INITIAL_CAPITAL,
        "equity": settings.INITIAL_CAPITAL,
        "daily_pnl": 0.0,
        "is_halted": False,
        "system_state": "TRADING",
        "circuit_state": "NORMAL",
    }

    broker = PaperBroker(
        initial_balance=settings.INITIAL_CAPITAL,
        maker_fee=settings.MAKER_FEE,
        taker_fee=settings.TAKER_FEE,
        slippage_bps=settings.SLIPPAGE_BPS,
        is_spot_mode=True,
    )

    risk_engine = RiskEngine(
        risk_per_trade=settings.RISK_PER_TRADE,
        daily_max_loss_usd=settings.DAILY_MAX_LOSS,
        max_open_positions=settings.MAX_OPEN_POSITIONS,
        is_spot_mode=True,
    )

    market_data = MarketDataService(symbols=settings.DEFAULT_SYMBOLS)
    market_data.start()

    command_bus = CommandBus(runtime_state=runtime_state, broker=broker)

    runner = AutonomousPaperTrader(
        command_bus_instance=command_bus,
        market_data_service=market_data,
        risk_engine=risk_engine,
    )
    runner.start()
    logger.info("Trading Worker loop is active and scanning real Binance spot markets.")

    stop_event = asyncio.Event()

    def handle_signal():
        logger.info("Termination signal received. Shutting down trading worker...")
        runner.stop()
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_signal)
        except NotImplementedError:
            # Signal handlers not implemented on Windows event loop
            pass

    try:
        while not stop_event.is_set():
            await asyncio.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        handle_signal()
    finally:
        await market_data.stop()
        logger.info("Trading Worker shutdown complete.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        sys.exit(0)
