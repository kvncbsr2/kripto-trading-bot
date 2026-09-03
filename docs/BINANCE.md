# KRIPTO AGENT — Binance Integration Architecture

## 1. Scope & Execution Guardrails
* **Primary Exchange**: Binance Spot
* **Connection Mode**: Real-Time Market Data via WebSocket + REST Fallback
* **Safety Lock**: `LIVE_TRADING=false` is enforced at application startup and within the execution engine.
* Under no circumstances are live orders placed on Binance during paper trading validation.

---

## 2. WebSocket Real-Time Pipeline
The dedicated `BinanceConnector` connects to Binance multiplexed stream endpoint:
`wss://stream.binance.com:9443/stream?streams=<streams>`

### Streams Subscribed
1. **Kline Stream**: `<symbol>@kline_<timeframe>` (e.g., `btcusdt@kline_15m`)
   - Emits real-time OHLCV updates.
   - Triggers feature calculation and strategy evaluation on candle close.
2. **Book Ticker Stream**: `<symbol>@bookTicker`
   - Emits top-of-book best bid and best ask in real-time.
   - Used for computing real-time spread (BPS) and accurate simulated entry prices.
3. **Mini Ticker / 24h Ticker**: `<symbol>@ticker`
   - Supplies 24h rolling volume, high, low, and percentage price change.

---

## 3. Resilience & Self-Healing
The connection implements a robust state machine:
```text
DISCONNECTED ──► CONNECTING ──► HEALTHY
      ▲                            │ (Network / Ping Drop)
      │                            ▼
      └────────────── RECONNECTING / RESUBSCRIBE
```
* **Heartbeat**: 20s WebSocket ping interval with 10s timeout.
* **Exponential Backoff**: Reconnection starts at 2.0s and scales up to 60.0s upon failure.
* **Historical Resync**: REST API (`fetch_ohlcv`) recovers any missing candles after prolonged outages.
