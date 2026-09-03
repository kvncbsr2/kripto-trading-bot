from typing import Any, Dict, List

from backtesting.runner import BacktestRunner
from shared.schemas import Candle


class WalkForwardValidator:
    """
    Executes rolling Walk-Forward analysis across historical periods to guard against overfitting.
    Splits data sequentially into Train, Validation, and Test windows.
    """

    def __init__(
        self,
        train_ratio: float = 0.6,
        val_ratio: float = 0.2,
        test_ratio: float = 0.2,
    ):
        self.train_ratio = train_ratio
        self.val_ratio = val_ratio
        self.test_ratio = test_ratio

    def run_walk_forward(
        self,
        candles: List[Candle],
        windows_count: int = 3,
    ) -> List[Dict[str, Any]]:
        n = len(candles)
        if n < 150:
            return [{"status": "INSUFFICIENT_DATA_FOR_WALK_FORWARD"}]

        results = []
        step = n // (windows_count + 1)

        for w in range(windows_count):
            start_idx = w * (step // 2)
            end_idx = min(n, start_idx + step * 2)
            subset = candles[start_idx:end_idx]

            sub_len = len(subset)
            train_end = int(sub_len * self.train_ratio)
            val_end = train_end + int(sub_len * self.val_ratio)

            train_candles = subset[:train_end]
            val_candles = subset[train_end:val_end]
            test_candles = subset[val_end:]

            runner = BacktestRunner()
            train_perf = runner.run(train_candles)
            val_perf = runner.run(val_candles)
            test_perf = runner.run(test_candles)

            results.append(
                {
                    "window": w + 1,
                    "train_trades": train_perf.get("total_trades", 0),
                    "train_return_pct": train_perf.get("total_return_pct", 0.0),
                    "val_trades": val_perf.get("total_trades", 0),
                    "val_return_pct": val_perf.get("total_return_pct", 0.0),
                    "test_trades": test_perf.get("total_trades", 0),
                    "test_return_pct": test_perf.get("total_return_pct", 0.0),
                    "test_profit_factor": test_perf.get("profit_factor", 0.0),
                    "test_win_rate": test_perf.get("win_rate", 0.0),
                }
            )

        return results
