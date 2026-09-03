# Strategy Discovery Engine & Tournament Architecture (Master V6)

## 1. Purpose
The **Strategy Discovery Engine** moves beyond static strategies to automatically generate, backtest, stress test, and rank algorithmic strategy variants using an automated tournament pipeline.

---

## 2. R10 Strategy Variants (V1 – V10)
1. **R10-V1**: RSI Divergence Only (Baseline)
2. **R10-V2**: RSI Divergence + EMA200 Trend Filter
3. **R10-V3**: RSI Divergence + Volume Confirmation
4. **R10-V4**: RSI Divergence + ATR Volatility Regime
5. **R10-V5**: RSI Divergence + ADX Momentum Filter ($ADX \ge 20$)
6. **R10-V6**: RSI Divergence + Market Structure Breaks
7. **R10-V7**: RSI Divergence + Multi-Regime Filter
8. **R10-V8**: RSI Divergence + BTC Market Correlation Context
9. **R10-V9**: RSI Divergence + Confirmation Breakout
10. **R10-V10**: RSI Divergence + Composite Multi-Factor Score

---

## 3. Automated Tournament & Overfitting Protection
Each generated candidate undergoes a multi-stage validation tournament:
1. **Purged Time-Series Partitioning**: 60% In-Sample, 20% Validation, 20% Out-of-Sample with a 5-bar Embargo window.
2. **Walk-Forward Stability**: In-sample training followed by unseen out-of-sample execution.
3. **Fee Stress Test**: Evaluated at 1x, 2x, and 3x fees. Strategies that fail under 2x fees are labeled `FEE_FRAGILE`.
4. **Slippage Drag Test**: Evaluated at 0, 5, 10, 20, and 50 bps slippage.
5. **Monte Carlo Resampling**: 5,000 permutations calculating 95th percentile drawdown.
6. **Robustness Score (0–100)**: Multi-dimensional weighting of OOS performance, parameter plateau stability, fee robustness, and drawdown resistance.
7. **Multiple Testing Penalty**: Tracks number of trials to protect against false discovery through brute-force data mining.
