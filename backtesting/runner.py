from typing import Any, Dict, List, Optional

from services.feature_engine.features import FeatureEngine
from services.performance_engine.metrics import PerformanceEngine
from services.regime_engine.detector import RegimeDetector
from services.risk_engine.risk_engine import RiskEngine
from services.strategy_engine.strategy_manager import StrategyManager
from shared.enums import PositionSide, PositionStatus, Timeframe
from shared.schemas import Candle, PortfolioState, Position


class BacktestRunner:
    """
    Deterministic historical backtest engine with zero look-ahead bias,
    realistic fees (0.1%), slippage (5 bps), and ATR position sizing.
    """

    def __init__(
        self,
        initial_capital: float = 5000.0,
        maker_fee: float = 0.001,
        taker_fee: float = 0.001,
        slippage_bps: float = 5.0,
    ):
        self.initial_capital = initial_capital
        self.maker_fee = maker_fee
        self.taker_fee = taker_fee
        self.slippage_bps = slippage_bps
        self.regime_detector = RegimeDetector()
        self.strategy_manager = StrategyManager()
        self.risk_engine = RiskEngine()

    def run(
        self,
        candles: List[Candle],
        symbol: str = "BTC/USDT",
        timeframe: Timeframe = Timeframe.M15,
    ) -> Dict[str, Any]:
        if len(candles) < 50:
            return {"status": "INSUFFICIENT_DATA", "total_trades": 0}

        balance = self.initial_capital
        equity = self.initial_capital
        open_position: Optional[Position] = None
        closed_positions: List[Position] = []

        # Warm-up period 40 candles
        for i in range(40, len(candles)):
            window = candles[: i + 1]
            current_candle = candles[i]

            # 1. Update open position stops and check exit
            if open_position is not None:
                # Check Stop Loss / Take Profit
                is_long = open_position.side.value == "LONG"
                hit_exit = False
                exit_price = 0.0

                if is_long:
                    if current_candle.low <= open_position.stop_loss:
                        hit_exit = True
                        exit_price = open_position.stop_loss
                    elif current_candle.high >= open_position.take_profit:
                        hit_exit = True
                        exit_price = open_position.take_profit
                else:  # Short
                    if current_candle.high >= open_position.stop_loss:
                        hit_exit = True
                        exit_price = open_position.stop_loss
                    elif current_candle.low <= open_position.take_profit:
                        hit_exit = True
                        exit_price = open_position.take_profit

                if hit_exit:
                    fee = exit_price * open_position.quantity * self.taker_fee
                    gross_pnl = (
                        (exit_price - open_position.entry_price) * open_position.quantity
                        if is_long
                        else (open_position.entry_price - exit_price) * open_position.quantity
                    )
                    net_pnl = gross_pnl - open_position.fees_paid - fee
                    open_position.realized_pnl = net_pnl
                    open_position.closed_at = current_candle.timestamp
                    open_position.status = PositionStatus.CLOSED
                    closed_positions.append(open_position)
                    balance += net_pnl
                    equity = balance
                    open_position = None
                    continue

            # 2. If no position open, evaluate features, regime, strategies
            feature_vec = FeatureEngine.get_latest_feature_vector(window, symbol, timeframe)
            if not feature_vec:
                continue

            regime = self.regime_detector.detect(feature_vec)
            signals = self.strategy_manager.evaluate(feature_vec, regime)

            if signals and open_position is None:
                best_sig = max(signals, key=lambda s: s.confidence)
                dummy_portfolio = PortfolioState(
                    balance=balance,
                    equity=equity,
                    open_positions=[] if open_position is None else [open_position],
                )
                decision = self.risk_engine.evaluate_signal(
                    best_sig, dummy_portfolio, current_candle.timestamp
                )

                if decision.approved and decision.calculated_size > 0:
                    is_buy = decision.direction.value == "LONG"
                    # Slippage
                    slip_factor = self.slippage_bps / 10000.0
                    fill_price = decision.entry_price * (
                        1 + slip_factor if is_buy else 1 - slip_factor
                    )
                    fee = fill_price * decision.calculated_size * self.taker_fee

                    open_position = Position(
                        position_id=f"bt_pos_{i}",
                        symbol=symbol,
                        side=PositionSide.LONG if is_buy else PositionSide.SHORT,
                        entry_price=fill_price,
                        quantity=decision.calculated_size,
                        current_price=fill_price,
                        stop_loss=decision.stop_loss,
                        take_profit=decision.take_profit,
                        fees_paid=fee,
                        strategy=best_sig.strategy,
                        opened_at=current_candle.timestamp,
                    )

        metrics = PerformanceEngine.calculate_metrics(
            closed_positions, initial_capital=self.initial_capital, current_equity=equity
        )
        return metrics
