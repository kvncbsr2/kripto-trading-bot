# KRIPTO AGENT — Market Data & Scanner Pipeline

## 1. Multi-Coin Universe & Discovery
The platform continuously tracks a universe of liquid USDT pairs:
* `BTC/USDT`, `ETH/USDT`, `BNB/USDT`, `SOL/USDT`, `XRP/USDT`, `DOGE/USDT`, `ADA/USDT`, `AVAX/USDT`, `LINK/USDT`

The `BinanceMarketScanner` monitors:
1. **24h Volume (USDT)**: Rejects coins with volume below `MIN_24H_VOLUME_USDT` (\$10,000,000).
2. **Spread (Basis Points)**: Rejects pairs whose bid-ask spread exceeds `MAX_SPREAD_BPS` (15 bps).
3. **Volatility %**: Measures 24h high/low range to prioritize active pairs.
4. **Composite Opportunity Score (0 - 100)**: Weights liquidity (30%), spread tightness (30%), volatility (20%), and technical alignment (20%).

---

## 2. Data Quality Engine
Incoming data is passed through `DataQualityEngine` before any feature or strategy processing:
* **OHLC Geometry**: Verifies that $High \ge Max(Open, Close)$ and $Low \le Min(Open, Close)$ with $Volume \ge 0$.
* **Duplicate Detection**: Identifies and drops repeated candle timestamps.
* **Out-of-Order Check**: Halts trading (`TRADING_LOCK`) if timestamp sequences flow backwards.
* **Stale Price Detection**: Triggers `DATA_QUALITY_WARNING` if feed lags by $> 180$ seconds.
* **Spread Anomalies**: Halts trading if spread is inverted ($Bid \ge Ask$) or exceeds 100 bps.

---

## 3. Storage Hierarchy
* **Redis Cache**: Low-latency cache storing the latest tick, bookTicker, order book, and opportunity rankings.
* **TimescaleDB / PostgreSQL**: Long-term relational and hypertable storage for analytical candles, orders, fills, and portfolio snapshots.
