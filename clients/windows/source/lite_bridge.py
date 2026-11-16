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
from core.plan_store import (
    list_plans as stored_plans, save_plan as stored_save_plan,
    load_plan as stored_load_plan, delete_plan as stored_delete_plan,
    save_switch_backup,
)
from core.preflight import inspect_readiness
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
        "target_time": str(strategy["target_time"]),
        "interval_seconds": list(strategy["interval_range"]),
        "max_cycles": int(strategy["max_cycles"]),
        "max_duration_minutes": int(strategy.get("max_duration_minutes", 480)),
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


def get_plans() -> dict:
    return {"plans": stored_plans(app_dir() / "plans")}


def store_plan(name: str) -> dict:
    stored_save_plan(app_dir() / "plans", name, load_config(ensure_config()))
    return get_plans()


def use_plan(name: str) -> dict:
    path = ensure_config()
    original = load_config(path)
    changed = stored_load_plan(app_dir() / "plans", name, original)
    backup = save_switch_backup(app_dir() / "plans", original)
    save_config_atomic(path, changed)
    return {**get_snapshot(), "backup_name": backup, "plans": stored_plans(app_dir() / "plans")}


def remove_plan(name: str) -> dict:
    stored_delete_plan(app_dir() / "plans", name)
    return get_plans()


def get_preflight() -> dict:
    # Existing preflight is entirely local; passing an empty env prevents
    # leaking or implicitly claiming an external session from this short-lived
    # sidecar. Logged-in status requires a separate live identity check.
    checks = inspect_readiness(load_config(ensure_config()), env={})
    result = [
        {"label": item.label, "passed": item.passed, "detail": item.detail,
         "severity": item.severity}
        for item in checks
    ]
    return {
        "source": "local_only",
        "checks": result,
        "blockers": sum(not item.passed and item.severity == "blocker" for item in checks),
        "warnings": sum(not item.passed and item.severity == "warning" for item in checks),
        "school_verified": False,
    }


def save_strategy(data: Any) -> dict:
    if not isinstance(data, dict):
        raise ValueError("strategy must be object")
    allowed = {
        "semester", "mode", "target_time", "interval_low", "interval_high",
        "max_cycles", "max_duration_minutes",
    }
    if set(data) != allowed:
        raise ValueError("strategy fields differ from whitelist")
    path = ensure_config()
    current = load_config(path)
    strategy = current["strategy"]
    changed = update_config(
        current,
        courses=current["courses"],
        semester=data["semester"],
        mode=data["mode"],
        target_time=data["target_time"],
        interval_low=data["interval_low"],
        interval_high=data["interval_high"],
        max_cycles=data["max_cycles"],
        max_duration_minutes=data["max_duration_minutes"],
    )
    # Reuses the original validation and persistence; never changes policy,
    # account fields, school protocol contracts or enrollment permissions.
    save_config_atomic(path, changed)
    return get_snapshot()


def main() -> int:
    try:
        if len(sys.argv) != 2:
            raise ValueError("invalid arguments")
        command = sys.argv[1]
        if command == "snapshot":
            result = get_snapshot()
        elif command == "preflight":
            result = get_preflight()
        elif command == "plans":
            result = get_plans()
        elif command in ("save-courses", "save-plan", "apply-plan", "delete-plan", "save-strategy"):
            request = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
            if len(request) > MAX_INPUT_BYTES:
                raise ValueError("request too large")
            payload = json.loads(request.decode("utf-8"))
            if command == "save-courses":
                result = save_courses(payload)
            elif command == "save-strategy":
                result = save_strategy(payload)
            else:
                if not isinstance(payload, dict) or set(payload) != {"name"}:
                    raise ValueError("invalid plan request")
                name = payload["name"]
                if command == "save-plan":
                    result = store_plan(name)
                elif command == "apply-plan":
                    result = use_plan(name)
                else:
                    result = remove_plan(name)
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
