# Strategy Promotion Gate & Validation Lifecycle (Master V6)

## 1. Strategy Lifecycle
All trading strategies adhere to an immutable lifecycle progression:

```text
IDEA → DRAFT → BACKTEST → IN_SAMPLE → VALIDATION → OUT_OF_SAMPLE → WALK_FORWARD → MONTE_CARLO → STRESS_TEST → PAPER_VALIDATION → PROMOTED
                                                                                                                        ↓
                                                                                                                REJECTED / OVERFIT
```

---

## 2. Promotion Gate Criteria
A strategy can only attain `PROMOTED` status if it strictly satisfies all pre-flight conditions:
* **Robustness Score**: $\ge 70.0 / 100$
* **Out-of-Sample Profit Factor**: $\ge 1.10$
* **Out-of-Sample Expectancy**: $> 0.0$ USD
* **Sample Size**: $\ge 15$ trades
* **Fee Stress Test**: Passed under 2x fee multiplier
* **Slippage Drag Test**: Passed under 20 bps execution drag
* **Monte Carlo 95th Percentile Drawdown**: $\le 15.0\%$
* **Lookahead Audit**: 0 violations detected

---

## 3. Backtest vs Live Paper Validation
During live paper execution, `PaperValidationEngine` continuously measures the difference between backtest expectations and real-time execution:
* **Expected vs Actual Fill**: Quantifies real slippage
* **Expected vs Actual PnL**: Measures execution drag
* **Execution Efficiency**: Graded as `ALIGNED` ($\ge 85\%$), `DEVIATION_WARNING` ($70-84\%$), or `EXECUTION_FRAGILE` ($<70\%$).
