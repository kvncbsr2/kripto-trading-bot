# KRIPTO AGENT — RSI Divergence Swing Engine

## 1. Mathematical Concept
RSI Divergence identifies exhaustion in prevailing price trends by detecting discrepancies between price trajectory and the Relative Strength Index (RSI 14).

### Regular Bullish Divergence (Long Setup)
* **Price**: Forms a **Lower Low (LL)** across swing pivots.
* **RSI**: Forms a **Higher Low (HL)** with RSI value remaining $< 50$.
* **Interpretation**: Downward price momentum is dissipating; buyers are absorbing liquidity at lower levels.

### Regular Bearish Divergence (Short Setup)
* **Price**: Forms a **Higher High (HH)** across swing pivots.
* **RSI**: Forms a **Lower High (LH)** with RSI value remaining $> 50$.
* **Interpretation**: Upward momentum is weakening; sellers are halting price expansion.

---

## 2. Multi-Factor Confirmation
Divergence alone is treated strictly as a **Candidate Signal**. To trigger an entry, the signal must achieve a **Signal Score $\ge 70/100$** through the confluence of:
1. **Trend / Key Moving Averages** (EMA 20/50/200 proximity)
2. **Volume Confirmation** (Volume Ratio $\ge 1.2$ on reversal bar)
3. **Market Structure** (Break of local swing high/low)
4. **Regime Alignment** (Acceptable volatility, avoidance of hostile regimes)

---

## 3. Causal Implementation Guarantee
The `RSIDivergenceDetector` uses a sliding evaluation window ($W = 4$ bars) to confirm swing pivots. Pivot detection lags the current bar by $W$ periods, ensuring **zero look-ahead bias** during historical simulations and real-time operations.
