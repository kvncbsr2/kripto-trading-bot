# R10 RSI Divergence Swing Strategy (Master V6)

## 1. Overview
The **R10 RSI Divergence Strategy** is a high-conviction swing trading methodology designed to detect structural momentum exhaustion points using price and RSI swings.

* **Primary Timeframe**: `1D` (Daily candles, configurable for `4h`, `1h`, `15m`)
* **RSI Engine**: Wilder's 14-period RSI (parameter space: 7, 9, 14, 21)
* **Pivot Detection**: Causal Pivot Engine (`left_bars=5`, `right_bars=5`)

---

## 2. Zero-Lookahead / Anti-Repaint Pivot Engine
A swing low pivot at index $T$ is **NOT** confirmed at index $T$.
* It is confirmed if and only if all 5 subsequent bars ($T+1 \dots T+5$) close without breaching the low at $T$.
* Therefore, the confirmation timestamp is strictly $T + 5$.
* Signals are generated **ONLY** at $T + 5$ when confirmation is reached.
* **Repainting is mathematically impossible**: Historical visualization may highlight the pivot at $T$, but the execution engine only acts at confirmation time $T+5$ and fills on the next executable bar ($T+6$).

```text
Candle T-5 ... T-1    Candle T (Pivot)    Candle T+1 ... T+4    Candle T+5 (CONFIRMATION)
     [Past]          [Candidate Low]           [Wait]           [PIVOT CONFIRMED -> SIGNAL]
```

---

## 3. Signal Formations

### Bullish Divergence (Long)
* **Price**: Forms a Lower Low ($Low_2 < Low_1$)
* **RSI**: Forms a Higher Low ($RSI_2 > RSI_1$ with $RSI_2 < 50.0$)
* **Confirmation**: Breakout above local confirmation level or RSI recovery.

### Bearish Divergence (Short)
* **Price**: Forms a Higher High ($High_2 > High_1$)
* **RSI**: Forms a Lower High ($RSI_2 < RSI_1$ with $RSI_2 > 50.0$)
* **Confirmation**: Breakdown below local confirmation level or RSI rejection.

---

## 4. Scoring & Risk Rules
* **Divergence Quality Score (0–100)**: Evaluates pivot separation (8–35 bars ideal), RSI delta magnitude ($\ge 4.0$ points), and price distance normalized by ATR.
* **Signal Score (0–100)**: Confluence of Divergence Quality (30%), Trend (20%), Volume (15%), Regime (15%), and Market Structure (10%).
* **Stops & Targets**:
  * Stop Loss: $Low_2 - 1.5 \times ATR$ (Long) / $High_2 + 1.5 \times ATR$ (Short)
  * Take Profit: Entry $\pm 2.0 \times R$ (Minimum RR: 1.5, Target: 2.0)
