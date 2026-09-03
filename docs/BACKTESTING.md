# KRIPTO AGENT — Backtesting & Walk-Forward Validation

## 1. Zero Look-Ahead Bias Guarantee
* Feature calculations are strictly causal.
* Indicators (EMA, ADX, RSI, ATR, BB, Swings) only inspect current and preceding candles.
* Divergence swing detection lags confirmation by $N$ bars to prevent future leaking.

---

## 2. Performance Metrics
The Backtest and Performance Engine computes:
* **Total Return (%)** and **Net PnL (\$)**
* **Win Rate (%)** and **Loss Rate (%)**
* **Profit Factor**: Gross Profit / Gross Loss
* **Expectancy**: Mathematical expected profit per trade
* **Max Drawdown (%)**
* **Sharpe Ratio** & **Sortino Ratio**
* **Largest Win** & **Largest Loss**
* **Total Fees & Slippage Paid**

---

## 3. Walk-Forward Testing
To prevent curve-fitting and parameter overfitting:
1. Divide historical data into sequential slices:
   * **Train**: 60%
   * **Validation**: 20%
   * **Test**: 20%
2. Roll forward the evaluation window across multiple market regimes.
3. Compare Out-of-Sample (Test) performance against In-Sample (Train) performance.

---

## 4. Monte Carlo Statistical Analysis
* Resamples trade PnLs over 1,000 - 5,000 iterations with replacement.
* Estimates **Expected Drawdown**, **95th Percentile Drawdown**, **Worst-Case Drawdown**, and the **Probability of Consecutive Losing Streaks ($\ge 3$)**.
