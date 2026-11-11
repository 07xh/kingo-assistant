"""Local-only course-plan snapshots; never persist school credentials or protocols.

A plan is an editable *preference*, not proof that a course exists or has a seat.
Restoring a plan preserves the current installation's protocol and auth settings.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
import json
import os
import secrets
from pathlib import Path
import tempfile

from core.desktop_settings import update_config

SCHEMA_VERSION = 1
MAX_PLAN_BYTES = 64 * 1024
ALLOWED_COURSE_FIELDS = ("key", "course_id", "class_id", "course_name", "teacher", "group", "time_slots")


def validate_plan_name(name: str) -> str:
    if not isinstance(name, str):
        raise ValueError("课程方案名称必须是文本")
    value = name.strip()
    if not 1 <= len(value) <= 40 or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError("方案名称应为 1–40 个可见字符")
    return value


def _location(directory: Path, name: str) -> Path:
    name = validate_plan_name(name)
    # Hashing the name avoids filename traversal, illegal Windows characters
    # and collisions between Unicode and ASCII naming conventions.
    return directory / (sha256(name.encode("utf-8")).hexdigest() + ".json")


def _directory(directory: Path) -> Path:
    directory = Path(directory)
    if directory.is_symlink():
        raise ValueError("课程方案目录不能是符号链接")
    directory.mkdir(parents=True, exist_ok=True)
    if not directory.is_dir():
        raise ValueError("课程方案目录无效")
    return directory


def _read_file(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_PLAN_BYTES:
        raise ValueError("课程方案文件不存在、过大或位置不安全")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError("课程方案内容无效") from exc
    if not isinstance(data, dict) or data.get("version") != SCHEMA_VERSION:
        raise ValueError("课程方案版本不受支持")
    if not isinstance(data.get("courses"), list) or not isinstance(data.get("strategy"), dict):
        raise ValueError("课程方案结构不完整")
    return data


def list_plans(directory: Path) -> list[str]:
    directory = _directory(directory)
    result = []
    for path in directory.glob("*.json"):
        try:
            data = _read_file(path)
            title = validate_plan_name(data.get("name"))
            if path == _location(directory, title):
                result.append(title)
        except (ValueError, OSError):
            continue  # Damaged plans cannot block the rest of the application.
    return sorted(result, key=str.casefold)


def make_plan(name: str, config: dict) -> dict:
    """Whitelist safe fields; intentionally do not copy auth/notify/protocol."""
    title = validate_plan_name(name)
    strategy = config["strategy"]
    courses = []
    for course in config["courses"]:
        courses.append({key: course[key] for key in ALLOWED_COURSE_FIELDS if key in course})
    return {
        "version": SCHEMA_VERSION,
        "name": title,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "semester": config["xnxq"],
        "courses": courses,
        "strategy": {
            "mode": strategy["mode"],
            "target_time": strategy["target_time"],
            "interval_range": list(strategy["interval_range"]),
            "max_cycles": strategy["max_cycles"],
            "max_duration_minutes": strategy.get("max_duration_minutes", 480),
        },
    }


def restore_plan(data: dict, current_config: dict) -> dict:
    if not isinstance(data, dict) or data.get("version") != SCHEMA_VERSION:
        raise ValueError("未知的课程方案")
    # Never let an imported snapshot override the live official domain,
    # verification flag, session, push destinations or server selectors.
    strategy = data["strategy"]
    interval = strategy["interval_range"]
    if not isinstance(interval, list) or len(interval) != 2:
        raise ValueError("课程方案检查间隔错误")
    if not all(type(v) is int for v in (*interval, strategy["max_cycles"],
                                     strategy.get("max_duration_minutes", 480))):
        raise ValueError("课程方案检查参数类型无效")
    raw = data["courses"]
    if not isinstance(raw, list) or any(not isinstance(course, dict) for course in raw):
        raise ValueError("课程方案课程表无效")
    courses = [{key: course[key] for key in ALLOWED_COURSE_FIELDS if key in course}
               for course in raw]
    return update_config(current_config, courses=courses,
                         semester=data["semester"], mode=strategy["mode"],
                         target_time=strategy["target_time"],
                         interval_low=interval[0], interval_high=interval[1],
                         max_cycles=strategy["max_cycles"],
                         max_duration_minutes=strategy.get("max_duration_minutes", 480))


def save_plan(directory: Path, name: str, config: dict) -> None:
    directory = _directory(directory)
    plan = make_plan(name, config)
    # Validate before writing, using the identical config validation as the UI.
    restore_plan(plan, config)
    path = _location(directory, plan["name"])
    if path.is_symlink():
        raise ValueError("课程方案文件不能是符号链接")
    encoded = (json.dumps(plan, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    if len(encoded) > MAX_PLAN_BYTES:
        raise ValueError("课程方案内容过大")
    temp = None
    try:
        with tempfile.NamedTemporaryFile(dir=directory, prefix=".kingo-", suffix=".tmp", delete=False) as fh:
            temp = Path(fh.name)
            os.chmod(temp, 0o600)
            fh.write(encoded)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(temp, path)
    finally:
        if temp is not None and temp.exists():
            temp.unlink()


def load_plan(directory: Path, name: str, current_config: dict) -> dict:
    directory = _directory(directory)
    title = validate_plan_name(name)
    data = _read_file(_location(directory, title))
    if data.get("name") != title:
        raise ValueError("方案文件名称校验失败")
    return restore_plan(data, current_config)


def delete_plan(directory: Path, name: str) -> None:
    path = _location(_directory(directory), name)
    if path.is_symlink():
        raise ValueError("不能删除符号链接方案")
    path.unlink()


def save_switch_backup(directory: Path, current_config: dict) -> str:
    """Preserve a separately restorable snapshot before every plan switch.

    Unlike a single 'last switch' slot, a sequence of changes does not
    overwrite previous recovery points. Backup names never include credentials.
    """
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    folder = _directory(directory)
    # Windows clocks can return identical microsecond timestamps for fast
    # consecutive calls. Never overwrite a prior backup on such a collision.
    for _ in range(32):
        name = "切换备份-" + timestamp + "-" + secrets.token_hex(4)
        if not _location(folder, name).exists():
            save_plan(folder, name, current_config)
            return name
    raise ValueError("无法安全创建唯一的课程方案恢复点")
