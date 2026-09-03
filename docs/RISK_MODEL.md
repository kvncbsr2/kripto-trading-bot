# KRIPTO AGENT — Risk Management Model

## 1. Capital Guardrails & Position Sizing
* **Virtual Initial Capital**: \$5,000.00
* **Risk Per Trade**: 0.5% (approx \$25.00 risk per trade)
* **Position Sizing Formula**:
  $$PositionSize = \frac{AccountEquity \times RiskPerTrade}{|EntryPrice - StopPrice|}$$
* **Maximum Capital Allocation Limit**: A single trade cannot exceed 25% of total account equity.
* **Maximum Open Positions**: 2 active positions max.
* **Maximum Trades Per Day**: 5 trades max.

---

## 2. Daily Risk Limits & Loss Lock
* **Daily Maximum Loss**: \$50.00 (1.0% of \$5,000).
* **State Transition**: If `daily_pnl <= -$50.00`, the system enters `DAILY_RISK_LOCK` (`LOCKED`). No new trades may be opened until the start of the next trading day.

---

## 3. Daily Profit Targets (SOFT vs HARD Mode)
* **Benchmark Target Range**: +\$20.00 to +\$100.00 net PnL / day.
* **Target Policy**:
  * **SOFT Mode (Default)**: Once +\$20.00 is reached, trading continues only for high-conviction signals (Signal Score $\ge$ 75/100).
  * **HARD Mode**: Once +\$100.00 is reached, trading halts immediately for the day.

---

## 4. Stop-Loss & Take-Profit Mechanics
* **Stop Loss**: Always required on every position. Calculated as:
  $$SL = Entry \pm (ATR \times 1.5)$$
* **Take Profit**: Minimum Risk-to-Reward ratio of **1:1.5**, preferred **1:2.0**.
* **Break-Even Adjustment**: When price reaches +1.0R in profit, the stop-loss is automatically adjusted to entry price (Break-Even).

---

## 5. Circuit Breaker States
1. **NORMAL**: All health checks pass, trading enabled.
2. **WARNING**: Stale market data feed or abnormal volatility detected.
3. **LOCKED**: Daily max loss breached or daily trade count exceeded.
4. **EMERGENCY**: Maximum drawdown ($\ge 10\%$) breached. All operations halt.
