"""Local-only IPC for the optional KingoAssistant Lite Tauri frontend.

Only exposes whitelisted course fields and safe configuration metadata.
Does not open a school session, contact the network or claim enrollment.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
from typing import Any

from core.desktop_settings import load_config, save_config_atomic, update_config
from main import validate

COURSE_COLUMNS = ("key", "course_id", "class_id", "course_name", "teacher", "group", "time_slots")
MAX_INPUT_BYTES = 32 * 1024


def app_dir() -> Path:
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if root:
            return Path(root) / "KingoAssistantReadOnly"
    return Path.home() / ".kingo-assistant-readonly"


def bundled_template() -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return root / "config.example.yaml"


def ensure_config() -> Path:
    directory = app_dir()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "config.yaml"
    if not target.is_file():
        source = bundled_template()
        if not source.is_file():
            raise FileNotFoundError("config template absent")
        shutil.copyfile(source, target)
    return target


def snapshot_from(config: dict) -> dict:
    validate(config, check_only=True)
    courses = []
    for row in config["courses"]:
        if not isinstance(row, dict):
            raise ValueError("course is not a mapping")
        courses.append({name: str(row.get(name) or "") for name in COURSE_COLUMNS})
    strategy = config["strategy"]
    return {
        "semester": str(config["xnxq"]),
        "courses": courses,
        "mode": str(strategy["mode"]),
        "interval_seconds": list(strategy["interval_range"]),
        "protocol_verified": config.get("protocol", {}).get("verified") is True,
        # The Lite bridge has not authenticated or queried the school.
        "login_status": "not_checked",
        "catalog_status": "not_checked",
        "automatic_submit_available": False,
        "data_source": "local_config",
    }


def get_snapshot() -> dict:
    return snapshot_from(load_config(ensure_config()))


def save_courses(raw_courses: Any) -> dict:
    """Update only approved local plan fields; keep all protocol gates untouched."""
    if not isinstance(raw_courses, list) or len(raw_courses) > 30:
        raise ValueError("course count")
    path = ensure_config()
    current = load_config(path)
    # The frontend only changes its seven approved visible fields. Preserve
    # ancillary fields owned by the existing course-plan engine by stable key.
    # Removing a course removes its metadata intentionally; no bulk overwrite.
    existing_by_key = {
        row["key"]: row for row in current["courses"]
        if isinstance(row, dict) and isinstance(row.get("key"), str)
    }
    cleaned = []
    for row in raw_courses:
        if not isinstance(row, dict):
            raise ValueError("course type")
        visible = {key: row.get(key, "") for key in COURSE_COLUMNS}
        old = existing_by_key.get(visible["key"], {})
        cleaned.append({**old, **visible})
    strategy = current["strategy"]
    updated = update_config(
        current,
        courses=cleaned,
        semester=current["xnxq"],
        mode=strategy["mode"],
        target_time=strategy["target_time"],
        interval_low=strategy["interval_range"][0],
        interval_high=strategy["interval_range"][1],
        max_cycles=strategy["max_cycles"],
        max_duration_minutes=strategy.get("max_duration_minutes"),
    )
    save_config_atomic(path, updated)
    return snapshot_from(load_config(path))


def main() -> int:
    try:
        if len(sys.argv) != 2:
            raise ValueError("invalid arguments")
        command = sys.argv[1]
        if command == "snapshot":
            result = get_snapshot()
        elif command == "save-courses":
            request = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
            if len(request) > MAX_INPUT_BYTES:
                raise ValueError("request too large")
            payload = json.loads(request.decode("utf-8"))
            result = save_courses(payload)
        else:
            raise ValueError("unsupported operation")
        print(json.dumps({"ok": True, "data": result}, ensure_ascii=False), flush=True)
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        # Never return raw exception details, config, credentials or school data.
        print(json.dumps({"ok": False, "error": "LOCAL_CONFIG_" + type(exc).__name__}), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
