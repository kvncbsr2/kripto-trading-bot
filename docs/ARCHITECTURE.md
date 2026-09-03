# KRIPTO AGENT — System Architecture V2

## Overview
**KRIPTO AGENT** is a production-oriented, autonomous algorithmic crypto trading platform designed specifically for deterministic risk-controlled **Paper Trading Validation**.

---

## 1. High-Level Data & Decision Flow

```text
Exchange Market Data (Binance REST / WS)
        │
        ▼
Data Validation & Normalization Layer
        │
        ▼
Feature Engine (Trend, Momentum, Volatility, Volume, Market Structure, RSI Divergence)
        │
        ▼
Market Regime Engine (BULL_TREND, BEAR_TREND, SIDEWAYS, HIGH_VOLATILITY, LOW_VOLATILITY)
        │
        ▼
Strategy Engine & Signal Scorer (Trend Following, Mean Reversion, RSI Divergence)
        │
        ▼
Opportunity Scoring (0 - 100 Quality Filter)
        │
        ▼
Central Risk Engine (Veto Power, 0.5% Sizing, $50 Daily Loss Lock, Circuit Breaker)
        │
        ▼
Paper Broker (5 bps Slippage, 0.1% Fees, Order Lifecycle, Break-Even & Trailing Stops)
        │
        ▼
Portfolio & Performance Engine (Equity, Drawdown, Daily PnL, Monte Carlo, Journal)
        │
        ▼
FastAPI Backend & Interactive Dashboard & Telegram Reporting
```

---

## 2. Component Directory Architecture

```text
KRIPTO AGENT/
├── apps/
│   ├── api/             # FastAPI backend with Swagger docs & /dashboard
│   └── dashboard/       # Next.js / TypeScript dashboard application
├── services/
│   ├── market_data/     # CCXT REST historical collector & live WebSocket client
│   ├── feature_engine/  # Indicators (EMA, ADX, RSI, ATR, BB, VWAP, Market Structure, RSI Divergence)
│   ├── regime_engine/   # Deterministic regime detector (BULL, BEAR, SIDEWAYS, HIGH/LOW VOL)
│   ├── strategy_engine/ # Trend Following, Mean Reversion, RSI Divergence
│   ├── signal_engine/   # 0-100 Signal Scorer & Opportunity Score
│   ├── risk_engine/     # ATR position sizing, stop validation, circuit breaker
│   ├── paper_broker/    # Simulated broker, fills, slippage, fees, stops & targets
│   ├── portfolio_engine/# Realized/unrealized PnL, equity tracking
│   ├── performance_engine/ # Metrics, Monte Carlo simulations, daily journals
│   └── notification_service/ # Telegram alert formatting & command handling
├── agents/              # Multi-agent consensus layer (Technical, Sentiment, Macro, Onchain, Risk)
├── backtesting/         # BacktestRunner & WalkForwardValidator (out-of-sample testing)
├── database/            # SQLAlchemy 2.0 async models, TimescaleDB hypertables, repositories
├── shared/              # Config, enums, schemas, structured JSON logging, utilities
├── tests/               # 100% passing pytest suites (unit, strategy, risk, integration, e2e)
├── scripts/             # 7-day validation runner (run_7day_experiment.py)
└── docs/                # Architectural & operational documentation
```

---

## 3. Strict Safety Invariants
1. **LIVE_TRADING = false**: Hard runtime exception thrown if live trading is ever flagged true during validation.
2. **Zero Real Money Risk**: Simulated broker accounts with $5,000 virtual balance.
3. **Risk Engine Veto**: Strategy signals and AI agent outputs never bypass risk limits.
