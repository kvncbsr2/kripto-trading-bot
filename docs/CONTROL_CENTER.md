# KRIPTO AGENT — Local Trading Control Center Architecture (Master V5)

## 1. Overview & Philosophy
The **Local Trading Control Center** elevates KRIPTO AGENT from a passive dashboard to an active, operational mission control platform running locally on the user's workstation. 

### Core Architectural Principle:
```text
USER ACTION
    ↓
LOCAL CONTROL DASHBOARD (HTML5/Tailwind/Next.js)
    ↓
COMMAND BUS (/api/agent/*, /api/risk/*, /api/strategies/*)
    ↓
READINESS GATE & SAFETY VALIDATION
    ↓
REAL ENGINE (PaperBroker, RiskEngine, Scanner, VectorBT, MonteCarlo)
    ↓
IMMUTABLE AUDIT LOG & REDIS/DATABASE
    ↓
REAL RESULT FEEDBACK
```

> **Zero Mock / Zero Fake Policy**: Every button and control triggers a concrete, verifiable backend method. There are no placeholder endpoints or simulated frontend-only states.

---

## 2. Command Bus (`services/command_bus/`)
The `CommandBus` dispatches incoming operator actions to underlying trading engines:
* `execute_start_agent()`: Pre-flight check via `ReadinessGate`. If passed, sets `system_state = TRADING`.
* `execute_pause_agent()`: Transitions system to `PAUSED`. Freezes new position entries while keeping existing stop-losses and take-profit orders actively managed.
* `execute_resume_agent()`: Resumes trading from `PAUSED`. Blocked if system is in `RISK_LOCK`.
* `execute_stop_agent()`: Gracefully halts all trading routines.
* `execute_emergency_stop()`: Immediate circuit breaker trip. Freezes order generation, cancels open limit orders, preserves existing protective stops, and locks system into `RISK_LOCK`.
* `execute_close_position(symbol)`: Executes simulated market fill at current bid/ask and closes paper position.
* `execute_cancel_order(order_id)`: Cancels active order.
* `execute_update_risk_config(params)`: Dynamically adjusts Risk Engine settings in memory and config.
* `execute_toggle_strategy(name, enabled)`: Enables or disables specific trading strategies.
* `execute_run_scanner()`: Executes real Binance spot scanner across monitored pairs.
* `execute_reset_experiment(confirmation=True)`: Resets virtual paper trading capital to \$5,000 baseline with safety confirmation.

---

## 3. Readiness Gate (`services/risk_engine/readiness_gate.py`)
Before the trading agent is allowed to start, the `ReadinessGate` evaluates 5 pre-flight invariants:
1. **Safety Lock**: `LIVE_TRADING` must be `False` and `PAPER_TRADING` must be `True`.
2. **Risk Limits**: Risk per trade must be between 0.1% and 5%; daily max loss between \$10 and \$500.
3. **Capital**: Virtual starting capital must be $\ge \$100$.
4. **Database Connectivity**: Database connection must be responsive.
5. **Universe Configuration**: At least one default trading pair must be configured.

If any invariant fails, agent startup is blocked and specific reasons are displayed in the dashboard.

---

## 4. Audit Trail (`services/command_bus/command_bus.py`)
Every action executed via the dashboard is recorded in the immutable `audit_log`:
* `timestamp`: ISO-8601 UTC timestamp
* `user`: Operator ID
* `action`: Action name (e.g. `START_AGENT`, `EMERGENCY_STOP`, `CLOSE_POSITION`)
* `parameters`: Action inputs
* `success`: Boolean result
* `result`: Output payload or error details
* Accessible via `GET /api/system/audit-logs` and viewed in the dashboard Audit tab.

---

## 5. Local Launch Instructions
```bash
# Windows Single-Command Launch:
start.bat
# or in PowerShell:
.\start.ps1

# Docker Compose Full Stack:
docker compose up -d

# Direct Dashboard Access:
http://localhost:8000/dashboard
```
