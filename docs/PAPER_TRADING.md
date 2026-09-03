# KRIPTO AGENT — Paper Broker & Cost Simulation

## 1. Execution Simulation
The Paper Broker runs in-memory and persists all operations into SQLite / PostgreSQL:
* **Order Types**: `MARKET`, `LIMIT`, `STOP_LOSS`, `TAKE_PROFIT`
* **Order Lifecycle**: `PENDING` $\rightarrow$ `FILLED` $\rightarrow$ `CLOSED` (or `CANCELLED`)
* **Immutable Audit Trail**: Every fill records fill ID, order ID, executed price, quantity, slippage amount, and fees.

---

## 2. Realistic Friction Modeling
* **Taker Fee**: 0.1% (0.001) applied on market entries and exits.
* **Maker Fee**: 0.1% (0.001) applied on resting limit orders.
* **Slippage**: 5 basis points (0.05% / 0.0005) applied to entry prices:
  * Buy orders: $Price \times (1 + SlippageBps/10000)$
  * Sell orders: $Price \times (1 - SlippageBps/10000)$
* **Net PnL Calculation**:
  $$NetPnL = GrossPnL - TotalFees - Slippage$$
No backtest or validation report is ever presented without factoring in fees and slippage.

---

## 3. Position Management
* Position tracker monitors unrealized and realized PnL on every candle tick.
* Trailing stops move to break-even once +1.0R is achieved.
* High/Low candle checks evaluate whether Stop-Loss or Take-Profit occurred during the interval.
