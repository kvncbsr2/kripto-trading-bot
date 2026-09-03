# KRIPTO AGENT — Security Architecture

## 1. Secrets Management
* No API keys, secret tokens, or passwords are hardcoded in the codebase.
* `.env` is ignored in `.gitignore`. A sanitized `.env.example` is maintained.
* Structured logging deliberately filters out and redacts credentials.

---

## 2. Hardcoded Live Trading Guardrails
```python
if settings.LIVE_TRADING:
    raise RuntimeError("CRITICAL SECURITY VIOLATION: LIVE TRADING IS DISABLED DURING VALIDATION.")
```
There is zero code execution path that can place an order on a live exchange in V2. Exchange connectivity is strictly read-only for market data.

---

## 3. Safe API Design
* FastAPI routes validate input models with Pydantic v2.
* Database operations use SQLAlchemy 2.0 parameterized queries to prevent SQL injection.
* Non-custodial architectural pattern: Even when exchange keys are added in later phases, withdrawal permissions are strictly prohibited.
