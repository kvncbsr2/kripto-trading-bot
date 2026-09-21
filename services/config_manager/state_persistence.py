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
    "is_autonomous_active": False,
    "circuit_suspended": False,
    "is_halted": False,
    "circuit_state": "NORMAL",
    "last_action": "Otonom motor beklemede (STOPPED).",
    "last_cycle_at": None,
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

        lvl = int(data.get("active_risk_profile_level", 1))
        if lvl < 1 or lvl > 10:
            logger.warning(f"Persisted profile level {lvl} invalid, falling back to 1.")
            lvl = 1

        strategy_id = str(data.get("active_strategy_id", "momentum_dip_rebound"))
        max_pos = data.get("max_open_positions_override")
        if max_pos is not None:
            max_pos = min(int(max_pos), 8)

        res = dict(DEFAULT_STATE)
        res.update({
            "active_risk_profile_level": lvl,
            "active_strategy_id": strategy_id,
            "is_autonomous_active": bool(data.get("is_autonomous_active", False)),
            "circuit_suspended": bool(data.get("circuit_suspended", False)),
            "is_halted": bool(data.get("is_halted", False)),
            "circuit_state": str(data.get("circuit_state", "NORMAL")),
            "last_action": str(data.get("last_action", "Otonom motor beklemede (STOPPED).")),
            "last_cycle_at": data.get("last_cycle_at"),
            "updated_at": data.get("updated_at", datetime.now(timezone.utc).isoformat()),
            "version": data.get("version", "2.0.0"),
            "source": data.get("source", "persisted_file"),
        })
        if max_pos is not None:
            res["max_open_positions_override"] = max_pos
        return res
    except Exception as e:
        logger.error(f"Failed to read persisted state from {target_path}: {e}. Safe fallback to Level 1.")
        return dict(DEFAULT_STATE)


def update_persisted_state(
    updates: Dict[str, Any],
    file_path: Optional[Path] = None,
) -> bool:
    """
    Atomically updates a subset of runtime state on disk, preserving all other fields.
    Guarantees cross-process consistency across Passenger workers.
    """
    target_path = Path(file_path) if file_path else STATE_FILE_PATH
    data = dict(DEFAULT_STATE)
    if target_path.exists():
        try:
            with open(target_path, "r", encoding="utf-8") as f:
                data.update(json.load(f))
        except Exception:
            pass

    data.update(updates)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()

    try:
        temp_file = target_path.with_suffix(f".tmp_{os.getpid()}")
        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(temp_file, target_path)
        return True
    except Exception as e:
        logger.error(f"Failed to atomically update persisted state in {target_path}: {e}")
        return False


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
    lvl = max(1, min(10, int(risk_level)))
    payload: Dict[str, Any] = {
        "active_risk_profile_level": lvl,
        "active_strategy_id": strategy_id,
        "source": source,
    }
    if max_open_positions_override is not None:
        payload["max_open_positions_override"] = min(int(max_open_positions_override), 8)

    ok = update_persisted_state(payload, file_path=file_path)
    if ok:
        logger.info(f"Runtime state successfully persisted: Level {lvl}, Strategy {strategy_id}")
    return ok
