# KRIPTO AGENT — 7-Day $5,000 Paper Trading Validation Experiment

## Objective
To empirically measure whether a deterministic combination of:
1. **Trend Following**,
2. **Mean Reversion**, and
3. **RSI Divergence Swing**

coupled with an **ATR-based 0.5% risk engine**, **\$50 daily loss lock**, **0.1% fees**, and **5 bps slippage** can generate consistent, positive expectancy on BTC/USDT and ETH/USDT before any real capital is deployed.

---

## Benchmark Targets
* **Virtual Initial Capital**: \$5,000.00
* **Daily Net PnL Target**: +\$20.00 to +\$100.00 (Soft/Hard modes)
* **Daily Max Loss Limit**: -\$50.00 (triggers `DAILY_RISK_LOCK`)

---

## Evaluation Verdicts
* **GREEN (PROMISING)**: Net PnL positive, Profit Factor $\ge 1.2$, Max DD $\le 5\%$, positive after +25% fee stress testing. Recommendation: Move to 30-day paper validation.
* **YELLOW (INSUFFICIENT DATA / MARGINAL)**: Inconsistent daily results or low sample size. Recommendation: Extend paper testing to 30-60 days.
* **RED (NOT VALIDATED)**: Negative net PnL, high drawdown, edge erased by fees. Recommendation: Discard or refactor strategy; DO NOT use real money.

---

## Running the Experiment
```bash
python -m scripts.run_7day_experiment
```
Outputs daily summaries for Days 1-7 and the comprehensive final report including Monte Carlo simulation, fee sensitivity analysis, and final verdict.
