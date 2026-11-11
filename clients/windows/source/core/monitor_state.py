"""Atomic, credential-free notification deduplication for local read-only monitoring.

Only target keys and SHA-256 fingerprints of *public course identifiers* are saved.
No raw HTML, Cookie, access token, school account or individual student data.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path


class StateFileError(ValueError):
    """A local state file is invalid; never overwrite it automatically."""


class MonitorStateStore:
    MAX_BYTES = 20_000
    MAX_ITEMS = 30

    def __init__(self, filename: str | Path):
        self.path = Path(filename)

    @staticmethod
    def fingerprint(course_id: str, class_id: str | None) -> str:
        blob = json.dumps([course_id, class_id], ensure_ascii=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("ascii")).hexdigest()

    def load(self) -> dict[str, str]:
        if self.path.is_symlink():
            raise StateFileError("状态文件不能是符号链接")
        if not self.path.exists():
            return {}
        if not self.path.is_file() or self.path.stat().st_size > self.MAX_BYTES:
            raise StateFileError("状态文件异常或过大")
        try:
            doc = json.loads(self.path.read_text(encoding="utf-8"))
            if (not isinstance(doc, dict) or set(doc) != {"schema", "active"}
                    or doc["schema"] != "kingo-readonly-notifications-v1"):
                raise StateFileError("状态文件版本未知")
            active = doc["active"]
            if not isinstance(active, dict) or len(active) > self.MAX_ITEMS:
                raise StateFileError("状态文件记录数量异常")
            if any(not isinstance(key, str) or not (1 <= len(key) <= 100)
                   or not isinstance(value, str) or len(value) != 64
                   or any(ch not in "0123456789abcdef" for ch in value)
                   for key, value in active.items()):
                raise StateFileError("状态文件内容无效")
            return active
        except (UnicodeError, json.JSONDecodeError, OSError) as error:
            raise StateFileError("状态文件损坏，请检查原文件，未自动覆盖") from error

    def save(self, active: dict[str, str]) -> None:
        if len(active) > self.MAX_ITEMS:
            raise StateFileError("状态记录超出上限")
        if self.path.is_symlink():
            raise StateFileError("拒绝覆写符号链接")
        data = json.dumps({"schema": "kingo-readonly-notifications-v1", "active": active},
                          ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        if len(data.encode("utf-8")) > self.MAX_BYTES:
            raise StateFileError("状态文件过大")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = None
        try:
            # tempfile creates the file with restrictive permissions on common OSes.
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", prefix=".kingo-",
                                             suffix=".tmp", dir=self.path.parent,
                                             delete=False) as fp:
                temp_path = Path(fp.name)
                fp.write(data)
                fp.flush()
                os.fsync(fp.fileno())
            if self.path.is_symlink():
                raise StateFileError("拒绝覆写符号链接")
            os.replace(temp_path, self.path)
        except OSError as error:
            raise StateFileError("本机状态保存失败") from error
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()
