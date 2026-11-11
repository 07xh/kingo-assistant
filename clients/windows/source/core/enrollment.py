"""Fail-closed automatic enrollment orchestration.

This module is an offline-testable state machine. It does not implement a
school-specific HTTP adapter. Live execution requires a separately reviewed
adapter that verifies the official catalog, current selection window, current
enrollments, and permitted write contract.

Writes are serial. A durable pending intent is recorded before submission.
An uncertain outcome is never automatically retried.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import Enum
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import tempfile
import threading
from typing import Protocol, Sequence

from core.course_models import (
    CourseTarget as Target, CourseOffering as Offering,
)
from core.enrollment_state import Transition


JOURNAL_VERSION = 2
MAX_JOURNAL_BYTES = 128 * 1024
MAX_JOURNAL_ATTEMPTS = 256
_ATTEMPT_STATES = frozenset({"pending", "rejected", "confirmed"})
_LOCAL_LOCKS: dict[str, threading.Lock] = {}
_LOCAL_LOCKS_GUARD = threading.Lock()


class EnrollmentNotReady(RuntimeError):
    """Live submission is disabled until all independent gates pass."""


class WindowState(str, Enum):
    OPEN = "open"
    CLOSED = "closed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class SubmitReceipt:
    """Transport acknowledgement is not proof of enrollment."""

    accepted: bool | None

    def __post_init__(self):
        if self.accepted is not None and type(self.accepted) is not bool:
            raise ValueError("提交确认必须为 true、false 或 unknown")


@dataclass(frozen=True)
class RunReport:
    states: dict[str, str]
    halted: bool
    transitions: dict[str, tuple[Transition, ...]] = field(default_factory=dict)


class VerifiedGateway(Protocol):
    """School adapter contract. No NCWU implementation is included."""

    official_catalog_verified: bool
    official_enrollment_verified: bool
    official_window_verified: bool
    official_write_verified: bool
    # Stable, non-PII account scope, used only as HMAC input and never persisted.
    account_scope: str
    enrollment_semester: str

    async def selection_window(self) -> WindowState: ...
    async def read_enrollments(self) -> set[tuple[str, str]]: ...
    async def list_offerings(self) -> Sequence[Offering]: ...
    async def read_schedule(self): ...
    async def submit_once(self, target: Target) -> SubmitReceipt: ...


class AttemptJournal:
    """Local journal with keyed fingerprints and crash-safe process locking.

    Version 2 stores only HMAC fingerprints and transition states. The random
    key is stored beside the journal; course/class IDs and account scope are
    never written. A v1 journal is migrated conservatively: old attempts are
    retained as legacy-pending and require official reconciliation.
    """

    def __init__(self, path: Path, semester: str, account_scope: str = "local-default"):
        self.path = Path(path)
        if not isinstance(semester, str) or not semester.strip():
            raise ValueError("学期不能为空")
        if (not isinstance(account_scope, str) or not account_scope.strip()
                or len(account_scope) > 256
                or any(ord(ch) < 32 for ch in account_scope)):
            raise ValueError("账户隔离标识无效")
        self.semester = semester.strip()
        self.account_scope = account_scope
        self.lock_path = self.path.with_name(self.path.name + ".lock")

    def _prepare(self) -> None:
        parent = self.path.parent
        if parent.is_symlink() or self.path.is_symlink() or self.lock_path.is_symlink():
            raise ValueError("选课状态路径不允许使用符号链接")
        parent.mkdir(parents=True, exist_ok=True)
        if parent.is_symlink() or self.path.is_symlink() or self.lock_path.is_symlink():
            raise ValueError("选课状态路径不安全")

    @staticmethod
    def _new_data() -> dict:
        salt = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
        return {
            "version": JOURNAL_VERSION,
            "salt": salt,
            "attempts": {},
            "legacy_attempts": [],
        }

    def _write(self, data: dict) -> None:
        self._prepare()
        encoded = (json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
        if len(encoded) > MAX_JOURNAL_BYTES:
            raise ValueError("本地提交记录过大，拒绝写入")
        fd = -1
        temp_path: Path | None = None
        try:
            fd, name = tempfile.mkstemp(prefix=".attempt-", dir=self.path.parent)
            temp_path = Path(name)
            with os.fdopen(fd, "wb") as stream:
                fd = -1
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, self.path)
            temp_path = None
            if os.name != "nt":
                dir_fd = os.open(self.path.parent, os.O_RDONLY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
        finally:
            if fd >= 0:
                os.close(fd)
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    def _read(self) -> dict:
        self._prepare()
        if not self.path.exists():
            data = self._new_data()
            self._write(data)
            return data
        if not self.path.is_file() or self.path.stat().st_size > MAX_JOURNAL_BYTES:
            raise ValueError("本地提交记录异常，拒绝执行")
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("提交记录无法读取，拒绝执行") from exc

        # Preserve fingerprints from the previous schema as unresolved attempts.
        if (isinstance(value, dict) and value.get("version") == 1
                and isinstance(value.get("attempts"), list)
                and all(isinstance(x, str) and len(x) == 64
                        and all(c in "0123456789abcdef" for c in x)
                        for x in value["attempts"])):
            if len(value["attempts"]) > MAX_JOURNAL_ATTEMPTS:
                raise ValueError("历史提交记录超过安全上限，拒绝自动迁移")
            migrated = self._new_data()
            migrated["legacy_attempts"] = list(dict.fromkeys(value["attempts"]))
            self._write(migrated)
            return migrated

        if not isinstance(value, dict) or value.get("version") != JOURNAL_VERSION:
            raise ValueError("提交记录版本无效，拒绝执行")
        try:
            salt = base64.urlsafe_b64decode(value["salt"].encode("ascii"))
        except (KeyError, AttributeError, UnicodeError, ValueError) as exc:
            raise ValueError("提交记录密钥无效，拒绝执行") from exc
        attempts = value.get("attempts")
        legacy = value.get("legacy_attempts", [])
        if (len(salt) != 32 or not isinstance(attempts, dict)
                or not isinstance(legacy, list)
                or len(attempts) + len(legacy) > MAX_JOURNAL_ATTEMPTS):
            raise ValueError("提交记录结构无效，拒绝执行")
        if any(not isinstance(key, str) or len(key) != 64
               or any(c not in "0123456789abcdef" for c in key)
               or not isinstance(status, str) or status not in _ATTEMPT_STATES
               for key, status in attempts.items()):
            raise ValueError("提交状态无效，拒绝执行")
        if any(not isinstance(key, str) or len(key) != 64
               or any(c not in "0123456789abcdef" for c in key)
               for key in legacy):
            raise ValueError("历史提交指纹无效，拒绝执行")
        # Validation accepts older V2 files without this optional list.
        # Normalize in memory so status/reserve/recovery cannot KeyError.
        value["legacy_attempts"] = legacy
        return value

    def _fingerprint(self, target: Target, data: dict) -> str:
        salt = base64.urlsafe_b64decode(data["salt"].encode("ascii"))
        payload = json.dumps(
            [self.semester, self.account_scope, target.course_id, target.class_id],
            ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")
        return hmac.new(salt, payload, hashlib.sha256).hexdigest()

    def _legacy_fingerprint(self, target: Target) -> str:
        # Matches the v1 format exactly; only used to fail closed during migration.
        payload = json.dumps(
            [self.semester, target.course_id, target.class_id],
            ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def status(self, target: Target) -> str | None:
        data = self._read()
        fingerprint = self._fingerprint(target, data)
        if fingerprint in data["attempts"]:
            return data["attempts"][fingerprint]
        if self._legacy_fingerprint(target) in data["legacy_attempts"]:
            return "legacy_pending"
        return None

    def was_attempted(self, target: Target) -> bool:
        return self.status(target) is not None

    def unresolved_fingerprints(self) -> set[str]:
        data = self._read()
        return {fingerprint for fingerprint, status in data["attempts"].items()
                if status == "pending"} | set(data["legacy_attempts"])

    def reserve_before_write(self, target: Target) -> None:
        data = self._read()
        fingerprint = self._fingerprint(target, data)
        if fingerprint in data["attempts"] or self._legacy_fingerprint(target) in data["legacy_attempts"]:
            raise EnrollmentNotReady("该教学班已有提交记录，须先核对官方结果")
        if len(data["attempts"]) + len(data["legacy_attempts"]) >= MAX_JOURNAL_ATTEMPTS:
            raise EnrollmentNotReady("本地提交记录达到上限，须人工核对")
        data["attempts"][fingerprint] = "pending"
        self._write(data)

    def _transition(self, target: Target, new_state: str) -> None:
        if new_state not in _ATTEMPT_STATES:
            raise ValueError("未知提交状态")
        data = self._read()
        fingerprint = self._fingerprint(target, data)
        current = data["attempts"].get(fingerprint)
        if current == new_state:
            return
        if current == "pending" and new_state in ("rejected", "confirmed"):
            data["attempts"][fingerprint] = new_state
            self._write(data)
            return
        # Official records override an earlier rejection if the course later
        # appears as enrolled; all other unexpected transitions fail closed.
        if current == "rejected" and new_state == "confirmed":
            data["attempts"][fingerprint] = new_state
            self._write(data)
            return
        if current is None and new_state == "confirmed":
            legacy = self._legacy_fingerprint(target)
            if legacy in data["legacy_attempts"]:
                data["legacy_attempts"].remove(legacy)
                data["attempts"][fingerprint] = "confirmed"
                self._write(data)
                return
        raise EnrollmentNotReady("提交记录状态冲突，须先人工核对")

    def record_rejected(self, target: Target) -> None:
        self._transition(target, "rejected")

    def record_confirmed(self, target: Target) -> None:
        self._transition(target, "confirmed")

    @contextmanager
    def exclusive(self):
        """OS-backed lock; an orphaned lock file is reusable after process exit."""
        self._prepare()
        lock_key = str(self.lock_path.resolve())
        with _LOCAL_LOCKS_GUARD:
            local_lock = _LOCAL_LOCKS.setdefault(lock_key, threading.Lock())
        if not local_lock.acquire(blocking=False):
            raise EnrollmentNotReady("本机已有选课任务正在运行")
        stream = None
        locked = False
        try:
            stream = self.lock_path.open("a+b")
            if os.name == "nt":
                import msvcrt
                stream.seek(0, os.SEEK_END)
                if stream.tell() == 0:
                    stream.write(b"\0")
                    stream.flush()
                stream.seek(0)
                try:
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                    locked = True
                except OSError as exc:
                    raise EnrollmentNotReady("已有另一个选课进程持有本机锁") from exc
            else:
                import fcntl
                try:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                except BlockingIOError as exc:
                    raise EnrollmentNotReady("已有另一个选课进程持有本机锁") from exc
            yield
        finally:
            try:
                if stream is not None:
                    if locked:
                        try:
                            if os.name == "nt":
                                import msvcrt
                                stream.seek(0)
                                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                            else:
                                import fcntl
                                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                        finally:
                            stream.close()
                    else:
                        stream.close()
            finally:
                local_lock.release()


def _validate_targets(targets: Sequence[Target], semester: str | None = None) -> None:
    if not 1 <= len(targets) <= 30:
        raise ValueError("自动选课需要 1–30 条明确目标")
    keys = set()
    identities = set()
    for item in targets:
        if not isinstance(item, Target):
            raise ValueError("选课目标类型错误")
        if semester and item.semester and item.semester != semester:
            raise ValueError("选课目标学期不一致")
        fields = (item.key, item.course_id, item.class_id)
        if any(not isinstance(x, str) or not x.strip() or x != x.strip()
               or len(x) > 100 or any(ord(ch) < 32 for ch in x) for x in fields):
            raise ValueError("必须提供无空格边界的有效课程代码和教学班编号")
        if (not isinstance(item.group, str) or len(item.group) > 100
                or any(ord(ch) < 32 for ch in item.group)):
            raise ValueError("志愿组名称无效")
        identity = (item.course_id, item.class_id)
        if item.key in keys or identity in identities:
            raise ValueError("目标课程或教学班重复")
        keys.add(item.key)
        identities.add(identity)


def _read_records(records) -> set[tuple[str, str]]:
    if not isinstance(records, (set, frozenset)):
        raise ValueError("官方选课记录格式异常")
    if any(not isinstance(item, tuple) or len(item) != 2
           or any(not isinstance(x, str) or not x.strip() or x != x.strip() for x in item)
           for item in records):
        raise ValueError("官方选课结果无法可靠匹配")
    return set(records)


def _validate_catalog(records) -> list[Offering]:
    if not isinstance(records, (list, tuple)):
        raise ValueError("官方课程目录格式异常")
    result = list(records)
    for item in result:
        if (not isinstance(item, Offering)
                or not isinstance(item.course_id, str) or not item.course_id.strip()
                or item.course_id != item.course_id.strip()
                or not isinstance(item.class_id, str) or not item.class_id.strip()
                or item.class_id != item.class_id.strip()
                or (item.remain is not None
                    and (type(item.remain) is not int or item.remain < 0))):
            raise ValueError("官方课程目录字段不可信")
    return result


@dataclass(frozen=True, slots=True)
class VerificationPolicy:
    """Bounded read-only result verification; never a write retry."""
    max_reads: int = 3
    interval_seconds: float = 60.0

    def __post_init__(self) -> None:
        if type(self.max_reads) is not int or not 1 <= self.max_reads <= 5:
            raise ValueError("核验次数需为 1–5")
        if (type(self.interval_seconds) not in (float, int)
                or not 0 <= self.interval_seconds <= 3600):
            raise ValueError("核验间隔无效")


class EnrollmentEngine:
    """V5.1-capability-gated engine with V2 FSM flow (offline only)."""

    def __init__(self, gateway: VerifiedGateway, journal: AttemptJournal,
                 *, user_opted_in: bool = False,
                 institution_permits_write: bool = False,
                 verification_policy: VerificationPolicy | None = None):
        self.gateway = gateway
        self.journal = journal
        self.user_opted_in = user_opted_in
        self.institution_permits_write = institution_permits_write
        self.verification_policy = verification_policy or VerificationPolicy()

    def _gate(self) -> None:
        capabilities = (
            "official_catalog_verified",
            "official_enrollment_verified",
            "official_window_verified",
            "official_write_verified",
        )
        scope = getattr(self.gateway, "account_scope", None)
        if (not isinstance(scope, str) or not scope.strip()
                or scope != self.journal.account_scope):
            raise EnrollmentNotReady("当前官方账户与本地提交记录隔离范围不一致")
        if not (self.user_opted_in is True and self.institution_permits_write is True
                and all(getattr(self.gateway, name, False) is True for name in capabilities)):
            raise EnrollmentNotReady("用户授权、学校许可及目录/窗口/已选/写入接口验收必须全部通过")

    def validate_ready(self) -> None:
        """Public, network-free V5.1 readiness gate for scheduled runner."""
        self._gate()

    async def _window(self) -> WindowState:
        state = await self.gateway.selection_window()
        return state if isinstance(state, WindowState) else WindowState.UNKNOWN

    async def reconcile_pending(self, targets: Sequence[Target]):
        """One permitted read-only reconciliation, even outside selection time.

        This method never uses the write gate, submits a course, checks the
        selection window, or clears an unresolved journal fence. A separately
        verified official readback adapter and matching account/term are
        mandatory. Only explicit official enrollment records can mark a
        local pending attempt as confirmed.
        """
        from core.enrollment_recovery import reconcile_pending
        return await reconcile_pending(self.gateway, self.journal, targets)

    async def run(self, targets: Sequence[Target]) -> RunReport:
        # Single engine entry point. Flow is split out for focused FSM testing;
        # it retains this module's V5.1 HMAC journal, lock and official gates.
        from core.enrollment_flow import run_enrollment
        return await run_enrollment(self, targets)
