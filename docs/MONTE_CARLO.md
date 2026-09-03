# KRIPTO AGENT — Monte Carlo Risk & Drawdown Simulation

## 1. Overview
The `MonteCarloSimulator` resamples trade return distributions across 1,000 to 5,000 randomized permutations with replacement. This eliminates sequence bias (the illusion that order of trades was favorable) and produces true statistical distributions of portfolio drawdown and losing streaks.

---

## 2. Key Metrics Computed
* **Expected Drawdown**: The mean maximum drawdown across all randomized trials.
* **95th Percentile Drawdown**: Drawdown level that is not exceeded in 95% of simulated market runs.
* **Worst-Case Drawdown**: Absolute maximum equity drop observed across all permutations.
* **Probability of Consecutive Losing Streaks ($\ge 3$)**: Probability of experiencing three or more consecutive stop-loss executions.

---

## 3. Mathematical Significance in V3 Validation
A strategy that produces $+\$100$ net profit on 10 trades might still have an unacceptable 95th percentile drawdown of 12% if trade order is randomized.
If the 95th percentile drawdown exceeds **10.0%** (the platform's emergency circuit limit), the strategy is classified as **RED (STRATEGY NOT VALIDATED)** regardless of nominal win rate.
