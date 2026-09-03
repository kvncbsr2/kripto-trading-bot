# KRIPTO AGENT — Strategy Engine Specifications

## Strategy A — Trend Following (`trend_following`)
* **Target Market Regime**: `BULL_TREND` (Long) or `BEAR_TREND` (Short)
* **Conditions for Long**:
  1. $EMA_{20} > EMA_{50} > EMA_{200}$
  2. $ADX \ge 23.0$ (Strong trending impulse)
  3. $45.0 \le RSI \le 68.0$ (Healthy bullish momentum without blow-off extreme)
  4. Price above $EMA_{50}$
* **Stop-Loss**: $Entry - (ATR \times 1.5)$
* **Take-Profit**: $Entry + (ATR \times 1.5 \times 2.0)$ (1:2.0 R:R)

---

## Strategy B — Mean Reversion (`mean_reversion`)
* **Target Market Regime**: Strictly `SIDEWAYS` or `LOW_VOLATILITY`
* **Veto Trigger**: Absolute veto if market regime is `HIGH_VOLATILITY`, `BULL_TREND`, or `BEAR_TREND`.
* **Conditions for Long**:
  1. $Price \le BollingerLowerBand \times 1.002$
  2. $RSI \le 32.0$ (Oversold)
* **Conditions for Short**:
  1. $Price \ge BollingerUpperBand \times 0.998$
  2. $RSI \ge 68.0$ (Overbought)
* **Targets**: Bollinger Middle Band (SMA 20) or 1:2.0 R:R.

---

## Strategy C — RSI Divergence Swing (`rsi_divergence`)
* **Target**: Swing tops and bottoms confirmed by regular divergence.
* **Bullish Divergence**:
  * Price forms a **Lower Low (LL)**
  * RSI forms a **Higher Low (HL)** with $RSI < 50$
* **Bearish Divergence**:
  * Price forms a **Higher High (HH)**
  * RSI forms a **Lower High (LH)** with $RSI > 50$
* **Scoring Filter**: Requires a composite Signal Score $\ge 55/100$ (evaluating Trend, Momentum, Divergence, Volume, Regime, Market Structure, and Volatility).

---

## Opportunity Scoring (0 - 100)
Measures market actionability:
* **0 - 30**: `IGNORE`
* **30 - 50**: `WATCH`
* **50 - 70**: `CONSIDER`
* **70 - 85**: `TRADE`
* **85 - 100**: `HIGH_CONVICTION`
