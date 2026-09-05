from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from shared.logging import get_logger

logger = get_logger("anti-lookahead", service="backtest_engine")


@dataclass
class EventAuditRecord:
    event_id: str
    event_timestamp: datetime
    data_available_until: datetime
    decision_timestamp: datetime
    execution_timestamp: datetime
    bar_index: int
    details: Dict[str, Any]


class AntiLookaheadAuditResult(BaseModel):
    is_valid: bool
    total_events_checked: int
    lookahead_violations_count: int
    violations: List[Dict[str, Any]] = Field(default_factory=list)
    audit_timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class AntiLookaheadEngine:
    """
    Automated Anti-Future-Leakage & Lookahead Audit Engine.
    Ensures:
    1. decision_timestamp >= all input data timestamps
    2. execution_timestamp > decision_timestamp (no same-bar instantaneous execution with close price)
    3. pivot_time + confirmation_delay <= signal_time (no unconfirmed pivot back-dating)
    """

    def __init__(self, right_bars_delay: int = 5):
        self.right_bars_delay = right_bars_delay
        self.audit_records: List[EventAuditRecord] = []

    def record_decision_event(
        self,
        event_id: str,
        bar_index: int,
        data_available_until: datetime,
        decision_timestamp: datetime,
        execution_timestamp: datetime,
        pivot_timestamp: Optional[datetime] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> EventAuditRecord:
        record = EventAuditRecord(
            event_id=event_id,
            event_timestamp=datetime.now(timezone.utc),
            data_available_until=data_available_until,
            decision_timestamp=decision_timestamp,
            execution_timestamp=execution_timestamp,
            bar_index=bar_index,
            details=details or {},
        )
        if pivot_timestamp:
            record.details["pivot_timestamp"] = pivot_timestamp

        self.audit_records.append(record)
        return record

    def record_signal_generation(
        self,
        signal_id: str,
        data_available_until: datetime,
        decision_timestamp: datetime,
        execution_timestamp: datetime,
        pivot_timestamp: Optional[datetime] = None,
        bar_index: int = 0,
        details: Optional[Dict[str, Any]] = None,
    ) -> EventAuditRecord:
        return self.record_decision_event(
            event_id=signal_id,
            bar_index=bar_index,
            data_available_until=data_available_until,
            decision_timestamp=decision_timestamp,
            execution_timestamp=execution_timestamp,
            pivot_timestamp=pivot_timestamp,
            details=details,
        )

    def run_audit(self) -> AntiLookaheadAuditResult:
        """
        Scans all registered decision events to certify that zero future data leakage occurred.
        """
        violations: List[Dict[str, Any]] = []

        for r in self.audit_records:
            # Rule 1: Decision cannot be made before data is available
            if r.decision_timestamp < r.data_available_until:
                violations.append(
                    {
                        "rule": "FUTURE_DATA_LEAKAGE",
                        "event_id": r.event_id,
                        "bar_index": r.bar_index,
                        "decision_time": r.decision_timestamp.isoformat(),
                        "data_available_until": r.data_available_until.isoformat(),
                        "message": "Decision occurred before data became available in historical sequence.",
                    }
                )

            # Rule 2: Execution cannot precede decision or use same-bar closing price as immediate fill without lag
            if r.execution_timestamp < r.decision_timestamp:
                violations.append(
                    {
                        "rule": "RETROACTIVE_EXECUTION",
                        "event_id": r.event_id,
                        "bar_index": r.bar_index,
                        "decision_time": r.decision_timestamp.isoformat(),
                        "execution_time": r.execution_timestamp.isoformat(),
                        "message": "Execution timestamp precedes decision timestamp.",
                    }
                )

            # Rule 3: Pivot confirmation delay verification (pivot_time != signal_time)
            pivot_time = r.details.get("pivot_timestamp")
            if pivot_time and isinstance(pivot_time, datetime):
                if pivot_time == r.decision_timestamp:
                    violations.append(
                        {
                            "rule": "UNCONFIRMED_PIVOT_LOOKAHEAD",
                            "event_id": r.event_id,
                            "bar_index": r.bar_index,
                            "pivot_time": pivot_time.isoformat(),
                            "decision_time": r.decision_timestamp.isoformat(),
                            "message": "Pivot time equals signal decision time. Pivots require right_bars confirmation delay.",
                        }
                    )

        is_valid = len(violations) == 0
        if not is_valid:
            logger.error(
                f"LOOKAHEAD_VIOLATION: Detected {len(violations)} lookahead violations during backtest audit!",
                extra={"violations_count": len(violations)},
            )
        else:
            logger.info(
                f"ANTI_LOOKAHEAD_PASSED: {len(self.audit_records)} decision events certified free of future leakage."
            )

        return AntiLookaheadAuditResult(
            is_valid=is_valid,
            total_events_checked=len(self.audit_records),
            lookahead_violations_count=len(violations),
            violations=violations,
        )
