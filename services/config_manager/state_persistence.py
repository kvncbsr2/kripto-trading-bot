"""
Authoritative State Persistence for KRIPTO AGENT.
Persists active Risk Profile and Strategy to disk across restarts.
Ensures zero state loss upon bot crash, reboot, or server restart.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from shared.logging import get_logger

logger = get_logger("state-persistence", service="config_manager")

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATE_FILE_PATH = PROJECT_ROOT / "kripto_runtime_profile_state.json"

DEFAULT_STATE: Dict[str, Any] = {
    "active_risk_profile_level": 1,
    "active_strategy_id": "r10_rsi_divergence",
    "updated_at": datetime.now(timezone.utc).isoformat(),
    "version": "2.0.0",
    "source": "system_default",
}


def load_persisted_profile_state() -> Dict[str, Any]:
    """
    Loads persisted runtime state from disk.
    If the file does not exist or is corrupted, safely falls back to Level 1 and logs.
    """
    if not STATE_FILE_PATH.exists():
        logger.info(f"No persisted state file found at {STATE_FILE_PATH}. Using default L1 baseline.")
        return dict(DEFAULT_STATE)

    try:
        with open(STATE_FILE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Validate structure
        lvl = int(data.get("active_risk_profile_level", 1))
        if lvl < 1 or lvl > 10:
            logger.warning(f"Persisted profile level {lvl} invalid, falling back to 1.")
            lvl = 1

        strategy_id = str(data.get("active_strategy_id", "r10_rsi_divergence"))

        return {
            "active_risk_profile_level": lvl,
            "active_strategy_id": strategy_id,
            "updated_at": data.get("updated_at", datetime.now(timezone.utc).isoformat()),
            "version": data.get("version", "2.0.0"),
            "source": data.get("source", "persisted_file"),
        }
    except Exception as e:
        logger.error(f"Failed to read persisted state from {STATE_FILE_PATH}: {e}. Safe fallback to Level 1.")
        return dict(DEFAULT_STATE)


def save_persisted_profile_state(
    risk_level: int,
    strategy_id: str = "r10_rsi_divergence",
    source: str = "api",
) -> bool:
    """
    Atomically persists runtime profile and strategy selection to disk.
    """
    lvl = max(1, min(10, int(risk_level)))
    payload = {
        "active_risk_profile_level": lvl,
        "active_strategy_id": strategy_id,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "version": "2.0.0",
        "source": source,
    }

    try:
        temp_file = STATE_FILE_PATH.with_suffix(".tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        
        # Atomic rename on POSIX/Windows (replace)
        os.replace(temp_file, STATE_FILE_PATH)
        logger.info(f"Runtime state successfully persisted to {STATE_FILE_PATH}: Level {lvl}, Strategy {strategy_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to persist state to {STATE_FILE_PATH}: {e}")
        return False
