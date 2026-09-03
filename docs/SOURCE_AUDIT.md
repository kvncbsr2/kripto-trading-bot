# KRIPTO AGENT — Source Installation Audit Matrix (Phase 0)

> Official Source-of-Truth Hierarchy & Dependency Compatibility Matrix as defined in Master Prompt V4.

---

## 1. Source-of-Truth Hierarchy
* **Level 1 (Highest Priority)**: **Binance Official Spot API** (`binance/binance-spot-api-docs`). All exchange behaviors, order types, rate limits, WebSocket stream contracts, book ticker semantics, and error codes strictly adhere to official Binance documentation.
* **Level 2 (Framework & Execution)**: FastAPI, Pydantic v2, SQLAlchemy 2.0, VectorBT, NumPy, Pandas, TimescaleDB, Redis.
* **Level 3 (Abstraction Utilities)**: CCXT Async (used as helper, never overriding Binance official behavior).
* **Level 4 (Prohibited for Crypto Market Data)**: `yfinance` is strictly barred from being used as a crypto market data source.

---

## 2. GitHub Source Audit Table

| Repository / Package | Purpose | Required? | Installed? | Version | Compatible? | License | Security Status | Used Where |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **binance/binance-spot-api-docs** | Official Binance Spot API specification | **PRIMARY** | ✅ Docs Reference | 2026 Latest | Yes | MIT / Public | Clean | `services/binance_connector/`, `services/market_data/` |
| **fastapi/fastapi** | High-performance async backend REST API | **REQUIRED** | ✅ Yes | 0.110.0 | Yes (Python 3.12) | MIT | Clean | `apps/api/` |
| **pydantic/pydantic** | Strict schema validation and data parsing | **REQUIRED** | ✅ Yes | 2.13.5 | Yes | MIT | Clean | `shared/schemas/`, `packages/shared_types/` |
| **sqlalchemy/sqlalchemy** | Async ORM & Database Access Layer | **REQUIRED** | ✅ Yes | 2.0.28 | Yes | MIT | Clean | `database/`, `database/models/` |
| **sqlalchemy/alembic** | Database migrations & version control | **REQUIRED** | ✅ Yes | 1.13.1 | Yes | MIT | Clean | `database/migrations/` |
| **postgres/postgres** | Relational primary database | **REQUIRED** | ✅ Configured | 16-alpine | Yes (Docker) | PostgreSQL | Clean | `infrastructure/postgres/` |
| **timescale/timescaledb** | Time-series hypertables for tick & candle data | **REQUIRED** | ✅ Configured | 2.14-pg16 | Yes (Docker) | Apache 2.0 / TSL | Clean | `infrastructure/docker/` |
| **redis/redis-py** | Real-time state, cache, orderbook & pub-sub | **REQUIRED** | ✅ Yes | 5.0.3 | Yes | MIT | Clean | `services/market_data/`, `shared/` |
| **numpy/numpy** | Vectorized mathematical & array operations | **REQUIRED** | ✅ Yes | 2.5.2 | Yes | BSD-3-Clause | Clean | `services/feature_engine/`, `monte_carlo.py` |
| **pandas-dev/pandas** | Time-series data manipulation & analysis | **REQUIRED** | ✅ Yes | 3.0.5 | Yes | BSD-3-Clause | Clean | `services/feature_engine/`, `backtesting/` |
| **polakowo/vectorbt** | Quantitative backtesting & portfolio simulation | **REQUIRED** | ✅ Yes | 1.1.0 | Yes | Apache 2.0 | Clean | `services/backtest_engine/` |
| **plotly/plotly.py** | Visualization engine (pinned < 6 for VectorBT) | **REQUIRED** | ✅ Yes | 5.24.1 | Yes | MIT | Clean | `services/backtest_engine/` |
| **pytest-dev/pytest** | Automated test runner & assertions | **REQUIRED** | ✅ Yes | 9.1.1 | Yes | MIT | Clean | `tests/` |
| **astral-sh/ruff** | Lightning-fast Python linter & formatter | **REQUIRED** | ✅ Yes | 0.16.6 | Yes | MIT | Clean | Entire Codebase |
| **python/mypy** | Static type analysis | **REQUIRED** | ✅ Yes | 2.3.1 | Yes | MIT | Clean | Entire Codebase |
| **prometheus/client_python** | Metrics instrumentation (`/metrics`) | **REQUIRED** | ✅ Yes | 0.26.0 | Yes | Apache 2.0 | Clean | `apps/api/`, `infrastructure/prometheus/` |
| **python-telegram-bot** | Telegram alert notifications & command control | **REQUIRED** | ✅ Yes | 22.8 | Yes | LGPL-3.0 | Clean | `services/notification_service/` |
| **ccxt/ccxt** | Secondary exchange abstraction & fallback | **OPTIONAL** | ✅ Yes | 4.5.77 | Yes | MIT | Clean | `services/market_data/collectors/` |
| **vercel/next.js** | Interactive frontend dashboard | **REQUIRED** | ✅ Scaffolded | 14.x | Yes (Node.js) | MIT | Clean | `apps/dashboard/` |
| **tradingview/lightweight-charts**| High-performance canvas chart rendering | **REQUIRED** | ✅ Included | 4.x | Yes | Apache 2.0 | Clean | `apps/api/app/static/dashboard.html` |
| **ranaroussi/yfinance** | Yahoo Finance wrapper | **PROHIBITED** | ❌ Excluded | N/A | N/A | Apache 2.0 | Not Used | Strictly forbidden for crypto market data |

---

## 3. Dependency Conflict Resolution Log
1. **Pydantic v2 vs Pydantic-Core**: Resolved by synchronizing `pydantic==2.13.5` with `pydantic-core==2.46.5`.
2. **VectorBT vs Plotly 7.0**: Plotly 7.0 removed `scattermapbox` in favor of `scattermap`, causing VectorBT template initialization to fail. Resolved by installing `plotly==5.24.1` (`plotly<6`), ensuring full stability across all VectorBT backtesting workflows.
3. **Database Dual-Mode**: SQLite/aiosqlite for local unit/integration tests and TimescaleDB/asyncpg for Docker production containerization.
