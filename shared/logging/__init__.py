import json
import logging
import sys
from collections import deque
from datetime import datetime, timezone
from typing import Any, Dict, List

LIVE_LOG_RECORDS: deque = deque(maxlen=300)


class MemoryLogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord):
        try:
            msg = record.getMessage()
            # Ignore internal websocket tick frame noise and poll request logs
            if any(noise in msg for noise in ("Duplicate candle timestamp", "Data quality rejection", "GET /api/v1/system/logs", "GET /api/v1/system/status")):
                return
            now = datetime.now(timezone.utc)
            entry = {
                "timestamp": now.isoformat(),
                "time": now.strftime("%H:%M:%S"),
                "level": record.levelname,
                "service": getattr(record, "service", "system"),
                "message": msg,
                "logger": record.name,
            }
            LIVE_LOG_RECORDS.append(entry)
        except Exception:
            pass


_memory_handler = MemoryLogHandler()


class JSONFormatter(logging.Formatter):
    def __init__(self, service_name: str = "kripto-agent"):
        super().__init__()
        self.service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        log_record: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "service": getattr(record, "service", self.service_name),
            "event": getattr(record, "event", record.getMessage()),
            "logger": record.name,
        }

        # Contextual metadata
        for field in ("symbol", "strategy", "trace_id", "extra_data"):
            if hasattr(record, field):
                log_record[field] = getattr(record, field)

        if record.exc_info:
            log_record["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_record)


def get_logger(name: str, service: str = "kripto-agent", level: str = "INFO") -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(getattr(logging, level.upper(), logging.INFO))
        stream_handler = logging.StreamHandler(sys.stdout)
        stream_handler.setFormatter(JSONFormatter(service_name=service))
        logger.addHandler(stream_handler)
        logger.addHandler(_memory_handler)
        logger.propagate = False
    return logger


def get_recent_logs(limit: int = 100) -> List[Dict[str, Any]]:
    """Returns the most recent in-memory logs (up to limit)."""
    logs = list(LIVE_LOG_RECORDS)
    return logs[-limit:]


def add_system_log(message: str, level: str = "INFO", service: str = "system"):
    """Explicitly injects a user-friendly system event into the live logs."""
    now = datetime.now(timezone.utc)
    LIVE_LOG_RECORDS.append({
        "timestamp": now.isoformat(),
        "time": now.strftime("%H:%M:%S"),
        "level": level.upper(),
        "service": service,
        "message": message,
        "logger": "system",
    })
