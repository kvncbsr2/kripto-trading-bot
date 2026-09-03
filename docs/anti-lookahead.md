# Anti-Lookahead & Anti-Future-Leakage Engine (Master V6)

## 1. Zero Future Leakage Guarantee
In quantitative backtesting, lookahead bias (peeking into future bars) is the single most common reason backtests produce unrealistic returns that fail in live production.

KRIPTO AGENT enforces strict anti-lookahead rules via `services/backtest_engine/anti_lookahead.py`:

```text
decision_timestamp >= data_available_until
execution_timestamp > decision_timestamp
pivot_timestamp + confirmation_delay <= signal_timestamp
```

---

## 2. Invariants Checked by Audit Engine
1. **No Future Candle Access**: Strategies cannot read high, low, or close prices from unclosed candles.
2. **Next Executable Bar Entry**: Orders are filled at next bar open or delayed execution, preventing instantaneous fills on the same candle's close.
3. **Causal Pivot Confirmation**: Swing pivots require right-side confirmation bars before being visible to any decision logic.
4. **Purged Embargo Windows**: Walk-forward and train/test splits enforce boundary embargo periods to eliminate cross-fold information bleeding.

---

## 3. Immediate Disqualification
If any backtest records a decision timestamp earlier than data availability, the engine marks the result as `LOOKAHEAD_VIOLATION`, sets the Robustness Score to 0.0, and rejects the strategy from ever being promoted.
