"""Offline-safe desktop configuration editing and privacy-preserving persistence.

The GUI never modifies school protocol verification or performs HTTP requests.
Secrets are intentionally removed when saving a configuration through the GUI;
users can provide them via process environment variables when permitted.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import os
import tempfile
from typing import Any

import yaml


MAX_CONFIG_SIZE = 256 * 1024
COURSE_FIELDS = ("key", "course_id", "class_id", "course_name", "teacher")


def load_config(path: Path) -> dict:
    if not path.is_file():
        raise ValueError("配置文件不存在")
    if path.stat().st_size > MAX_CONFIG_SIZE:
        raise ValueError("配置文件过大")
    with path.open("r", encoding="utf-8") as handle:
        result = yaml.safe_load(handle)
    if not isinstance(result, dict):
        raise ValueError("配置文件根节点必须是映射")
    return result


def _field(value: Any, *, name: str, limit: int = 120) -> str:
    if value is None:
        return ""
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise ValueError(f"{name} 不是有效文本")
    value = str(value).strip()
    if len(value) > limit or any(ord(ch) < 32 for ch in value):
        raise ValueError(f"{name} 包含非法字符或过长")
    return value


def normalize_course(entry: dict) -> dict:
    if not isinstance(entry, dict):
        raise ValueError("课程项目必须为对象")
    result = {name: _field(entry.get(name), name=name) for name in COURSE_FIELDS}
    if not result["key"]:
        raise ValueError("志愿标识不能为空")
    if not any(result[k] for k in ("course_id", "course_name", "teacher")):
        raise ValueError("至少填写课程代码、课程名称或任课教师")
    # Retain safe ancillary fields and validate user-entered timetable hints.
    group = _field(entry.get("group"), name="备选组", limit=60)
    slots = _field(entry.get("time_slots"), name="上课节次", limit=200)
    from core.conflicts import parse_slots
    parse_slots(slots)
    return {**entry, **result, "group": group, "time_slots": slots}


def update_config(config: dict, *, courses: list[dict], semester: str,
                  mode: str, target_time: str, interval_low: int,
                  interval_high: int, max_cycles: int,
                  max_duration_minutes: int | None = None) -> dict:
    """Return a validated copy; nothing is written or sent over the network."""
    copy = deepcopy(config)
    if not isinstance(courses, list) or not 0 <= len(courses) <= 30:
        raise ValueError("草稿允许 0 门，最多设置 30 门目标课程")
    normalized = [normalize_course(item) for item in courses]
    keys = [item["key"] for item in normalized]
    if len(set(keys)) != len(keys):
        raise ValueError("志愿标识不允许重复")
    copy["courses"] = normalized
    copy["xnxq"] = _field(semester, name="学年学期", limit=60)
    if not copy["xnxq"]:
        raise ValueError("学年学期不能为空")
    if mode not in ("monitor", "burst"):
        raise ValueError("监测模式无效")
    if not all(type(n) is int for n in (interval_low, interval_high, max_cycles)):
        raise ValueError("间隔与次数必须为整数")
    copy["strategy"]["mode"] = mode
    copy["strategy"]["target_time"] = _field(target_time, name="计划检查时间", limit=60)
    copy["strategy"]["interval_range"] = [interval_low, interval_high]
    copy["strategy"]["max_cycles"] = max_cycles
    if max_duration_minutes is not None:
        copy["strategy"]["max_duration_minutes"] = max_duration_minutes
    # Preserve the read-only invariant even for imported configurations.
    copy["strategy"]["dry_run"] = True
    from main import validate
    validate(copy, check_only=True)
    return copy


def scrub_secrets(config: dict) -> tuple[dict, tuple[str, ...]]:
    copy = deepcopy(config)
    redacted = []
    for section, key in (("auth", "cookie"),
                         ("notify", "pushplus_token"),
                         ("notify", "serverchan_token")):
        content = copy.get(section)
        if isinstance(content, dict) and content.get(key):
            redacted.append(f"{section}.{key}")
            content[key] = ""
    return copy, tuple(redacted)


def save_config_atomic(path: Path, config: dict) -> tuple[str, ...]:
    """Atomic local save, no legacy plaintext-secret backup files."""
    safe, redacted = scrub_secrets(config)
    from main import validate
    validate(safe, check_only=True)
    contents = yaml.safe_dump(safe, allow_unicode=True, sort_keys=False)
    if len(contents.encode("utf-8")) > MAX_CONFIG_SIZE:
        raise ValueError("配置文件过大")
    path = Path(path)
    if not path.parent.is_dir():
        raise ValueError("配置目录不存在")
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         prefix=".kingo-", suffix=".tmp",
                                         dir=path.parent, delete=False) as stream:
            temp_path = Path(stream.name)
            os.chmod(temp_path, 0o600)
            stream.write(contents)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path is not None and temp_path.exists():
            temp_path.unlink()
    return redacted


def resolve_state_file(config: dict, user_directory: Path) -> dict:
    """Runtime-only: relative notification state paths stay under user data dir."""
    copy = deepcopy(config)
    raw = copy.get("strategy", {}).get("state_file")
    if not raw:
        return copy
    if not isinstance(raw, str):
        raise ValueError("状态文件路径无效")
    target = Path(raw).expanduser()
    if not target.is_absolute():
        root = user_directory.resolve()
        target = (root / target).resolve()
        if not target.is_relative_to(root):
            raise ValueError("相对状态文件路径不能离开应用数据目录")
    copy["strategy"]["state_file"] = str(target)
    return copy


def status_changes(old: dict[str, str], new: dict[str, str]) -> list[str]:
    """Return newly available target keys for local GUI attention only."""
    phrase = "发现余量"
    return [key for key, value in new.items()
            if phrase in value and phrase not in old.get(key, "")]
