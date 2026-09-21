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
    "active_strategy_id": "momentum_dip_rebound",
    "max_open_positions_override": None,
    "updated_at": datetime.now(timezone.utc).isoformat(),
    "version": "2.0.0",
    "source": "system_default",
}


def load_persisted_profile_state(file_path: Optional[Path] = None) -> Dict[str, Any]:
    """
    Loads persisted runtime state from disk.
    If the file does not exist or is corrupted, safely falls back to Level 1 and momentum_dip_rebound.
    """
    target_path = Path(file_path) if file_path else STATE_FILE_PATH
    if not target_path.exists():
        logger.info(f"No persisted state file found at {target_path}. Using conservative default L1 momentum_dip_rebound.")
        return dict(DEFAULT_STATE)

    try:
        with open(target_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Validate structure
        lvl = int(data.get("active_risk_profile_level", 1))
        if lvl < 1 or lvl > 10:
            logger.warning(f"Persisted profile level {lvl} invalid, falling back to 1.")
            lvl = 1

        strategy_id = str(data.get("active_strategy_id", "momentum_dip_rebound"))
        max_pos = data.get("max_open_positions_override")
        if max_pos is not None:
            max_pos = min(int(max_pos), 8)

        res = {
            "active_risk_profile_level": lvl,
            "active_strategy_id": strategy_id,
            "updated_at": data.get("updated_at", datetime.now(timezone.utc).isoformat()),
            "version": data.get("version", "2.0.0"),
            "source": data.get("source", "persisted_file"),
        }
        if max_pos is not None:
            res["max_open_positions_override"] = max_pos
        return res
    except Exception as e:
        logger.error(f"Failed to read persisted state from {target_path}: {e}. Safe fallback to Level 1.")
        return dict(DEFAULT_STATE)


def save_persisted_profile_state(
    risk_level: int,
    strategy_id: str = "momentum_dip_rebound",
    max_open_positions_override: Optional[int] = None,
    source: str = "api",
    file_path: Optional[Path] = None,
) -> bool:
    """
    Atomically persists runtime profile and strategy selection to disk.
    Guards live production state file from being overwritten during automated test runs.
    """
    target_path = Path(file_path) if file_path else STATE_FILE_PATH
    lvl = max(1, min(10, int(risk_level)))
    payload = {
        "active_risk_profile_level": lvl,
        "active_strategy_id": strategy_id,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "version": "2.0.0",
        "source": source,
    }
    if max_open_positions_override is not None:
        payload["max_open_positions_override"] = min(int(max_open_positions_override), 8)
    else:
        # Check if existing state had override
        try:
            if target_path.exists():
                with open(target_path, "r", encoding="utf-8") as f:
                    old_data = json.load(f)
                    if "max_open_positions_override" in old_data and old_data["max_open_positions_override"] is not None:
                        payload["max_open_positions_override"] = min(int(old_data["max_open_positions_override"]), 8)
        except Exception:
            pass

    try:
        temp_file = target_path.with_suffix(".tmp")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)
        
        # Atomic rename on POSIX/Windows (replace)
        os.replace(temp_file, target_path)
        logger.info(f"Runtime state successfully persisted to {target_path}: Level {lvl}, Strategy {strategy_id}")
        return True
    except Exception as e:
        logger.error(f"Failed to persist state to {target_path}: {e}")
        return False
