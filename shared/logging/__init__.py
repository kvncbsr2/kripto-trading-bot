import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Dict


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
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JSONFormatter(service_name=service))
        logger.addHandler(handler)
        logger.propagate = False
    return logger
