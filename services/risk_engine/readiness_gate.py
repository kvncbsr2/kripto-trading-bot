from datetime import datetime, timezone
from typing import Any, Dict, List

from shared.config import get_settings
from shared.logging import get_logger

logger = get_logger("readiness-gate", service="risk_engine")
settings = get_settings()


class ReadinessGate:
    """
    Evaluates system pre-flight conditions before paper trading starts.
    Blocks execution if critical invariants are violated.
    """

    @classmethod
    async def evaluate(cls, db_session=None) -> Dict[str, Any]:
        checks: Dict[str, bool] = {}
        reasons: List[str] = []

        # 1. Safety Guardrail Check
        if settings.LIVE_TRADING:
            checks["safety_lock"] = False
            reasons.append(
                "LIVE_TRADING is enabled! Must be FALSE during paper trading validation."
            )
        elif not settings.PAPER_TRADING:
            checks["safety_lock"] = False
            reasons.append("PAPER_TRADING is disabled! Must be TRUE.")
        else:
            checks["safety_lock"] = True

        # 2. Risk Limits Validated
        if settings.RISK_PER_TRADE <= 0 or settings.RISK_PER_TRADE > 0.05:
            checks["risk_limits"] = False
            reasons.append(
                f"Invalid risk_per_trade: {settings.RISK_PER_TRADE}. Must be between 0.1% and 5%."
            )
        elif settings.DAILY_MAX_LOSS < 10.0 or settings.DAILY_MAX_LOSS > 500.0:
            checks["risk_limits"] = False
            reasons.append(
                f"Invalid daily_max_loss: ${settings.DAILY_MAX_LOSS}. Must be between $10 and $500."
            )
        else:
            checks["risk_limits"] = True

        # 3. Capital Validated
        if settings.INITIAL_CAPITAL < 100.0:
            checks["capital"] = False
            reasons.append(
                f"Insufficient virtual capital: ${settings.INITIAL_CAPITAL}. Must be >= $100."
            )
        else:
            checks["capital"] = True

        # 4. Database Check
        if db_session is not None:
            try:
                from sqlalchemy import text

                await db_session.execute(text("SELECT 1"))
                checks["database"] = True
            except Exception as e:
                checks["database"] = False
                reasons.append(f"Database connection failed: {str(e)}")
        else:
            checks["database"] = True  # Local mode fallback

        # 5. Core Universe Symbols Configured
        if not settings.DEFAULT_SYMBOLS or len(settings.DEFAULT_SYMBOLS) < 1:
            checks["symbols_configured"] = False
            reasons.append("No default symbols configured for trading.")
        else:
            checks["symbols_configured"] = True

        ready = all(checks.values())

        return {
            "ready": ready,
            "checks": checks,
            "blocking_reasons": reasons,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
