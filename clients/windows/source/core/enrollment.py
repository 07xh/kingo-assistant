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
from dataclasses import dataclass
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
class Target:
    key: str
    course_id: str
    class_id: str
    group: str = ""


@dataclass(frozen=True)
class Offering:
    course_id: str
    class_id: str
    remain: int | None


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


class VerifiedGateway(Protocol):
    """School adapter contract. No NCWU implementation is included."""

    official_catalog_verified: bool
    official_enrollment_verified: bool
    official_window_verified: bool
    official_write_verified: bool
    # Stable, non-PII account scope, used only as HMAC input and never persisted.
    account_scope: str

    async def selection_window(self) -> WindowState: ...
    async def read_enrollments(self) -> set[tuple[str, str]]: ...
    async def list_offerings(self) -> Sequence[Offering]: ...
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
               or status not in _ATTEMPT_STATES
               for key, status in attempts.items()):
            raise ValueError("提交状态无效，拒绝执行")
        if any(not isinstance(key, str) or len(key) != 64
               or any(c not in "0123456789abcdef" for c in key)
               for key in legacy):
            raise ValueError("历史提交指纹无效，拒绝执行")
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
            local_lock.release()


def _validate_targets(targets: Sequence[Target]) -> None:
    if not 1 <= len(targets) <= 30:
        raise ValueError("自动选课需要 1–30 条明确目标")
    keys = set()
    identities = set()
    for item in targets:
        if not isinstance(item, Target):
            raise ValueError("选课目标类型错误")
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


class EnrollmentEngine:
    """Serial priority/fallback state machine with official-window gating."""

    def __init__(self, gateway: VerifiedGateway, journal: AttemptJournal,
                 *, user_opted_in: bool = False, institution_permits_write: bool = False):
        self.gateway = gateway
        self.journal = journal
        self.user_opted_in = user_opted_in
        self.institution_permits_write = institution_permits_write

    def _gate(self) -> None:
        capabilities = (
            "official_catalog_verified",
            "official_enrollment_verified",
            "official_window_verified",
            "official_write_verified",
        )
        if not (self.user_opted_in is True and self.institution_permits_write is True
                and all(getattr(self.gateway, name, False) is True for name in capabilities)):
            raise EnrollmentNotReady("用户授权、学校许可及目录/窗口/已选/写入接口验收必须全部通过")

    async def _window(self) -> WindowState:
        state = await self.gateway.selection_window()
        if not isinstance(state, WindowState):
            return WindowState.UNKNOWN
        return state

    async def run(self, targets: Sequence[Target]) -> RunReport:
        targets = list(targets)
        _validate_targets(targets)
        self._gate()
        states = {t.key: "未执行" for t in targets}

        with self.journal.exclusive():
            # The HAR observed an explicit "not a valid selection window" gate.
            # The official window must be OPEN before catalog or write work begins.
            try:
                window = await self._window()
                if window is not WindowState.OPEN:
                    label = "学校选课窗口未开放" if window is WindowState.CLOSED else "无法确认学校选课窗口"
                    return RunReport({t.key: label for t in targets}, True)
                enrolled = _read_records(await self.gateway.read_enrollments())
                catalog = _validate_catalog(await self.gateway.list_offerings())
            except Exception:
                return RunReport({t.key: "无法确认官方状态" for t in targets}, True)

            satisfied = {t.group for t in targets if t.group
                         and (t.course_id, t.class_id) in enrolled}
            refresh_catalog = False

            for target in targets:
                identity = (target.course_id, target.class_id)
                journal_state = self.journal.status(target)

                if identity in enrolled:
                    if journal_state in ("pending", "rejected"):
                        self.journal.record_confirmed(target)
                    states[target.key] = "已在官方选课记录中"
                    if target.group:
                        satisfied.add(target.group)
                    continue

                if target.group and target.group in satisfied:
                    states[target.key] = "同组志愿已有成功课程"
                    continue
                if any(course_id == target.course_id for course_id, _ in enrolled):
                    states[target.key] = "该课程已有其他教学班，跳过"
                    continue

                if journal_state in ("pending", "legacy_pending"):
                    states[target.key] = "存在未核实提交，禁止自动重发"
                    return RunReport(states, True)
                if journal_state == "confirmed":
                    states[target.key] = "历史确认与当前官方记录不一致"
                    return RunReport(states, True)
                if journal_state == "rejected":
                    states[target.key] = "此前已明确拒绝，跳过重复提交"
                    continue

                if refresh_catalog:
                    try:
                        catalog = _validate_catalog(await self.gateway.list_offerings())
                    except Exception:
                        states[target.key] = "无法刷新官方目录"
                        return RunReport(states, True)
                    refresh_catalog = False

                matches = [item for item in catalog
                           if (item.course_id, item.class_id) == identity]
                if len(matches) != 1:
                    states[target.key] = "未找到唯一教学班"
                    continue
                if matches[0].remain is None:
                    states[target.key] = "真实余量未知"
                    continue
                if matches[0].remain == 0:
                    states[target.key] = "暂无余量"
                    continue

                # Re-check the official window immediately before every write.
                try:
                    window = await self._window()
                except Exception:
                    window = WindowState.UNKNOWN
                if window is not WindowState.OPEN:
                    states[target.key] = ("学校选课窗口已关闭" if window is WindowState.CLOSED
                                          else "提交前无法确认学校选课窗口")
                    return RunReport(states, True)

                # Persist a pending intent before the only allowed submission.
                self.journal.reserve_before_write(target)
                try:
                    receipt = await self.gateway.submit_once(target)
                    if not isinstance(receipt, SubmitReceipt):
                        raise ValueError("写入确认响应结构异常")
                    enrolled = _read_records(await self.gateway.read_enrollments())
                except Exception:
                    states[target.key] = "写入结果不确定，已停止防止重复提交"
                    return RunReport(states, True)

                if identity in enrolled:
                    self.journal.record_confirmed(target)
                    states[target.key] = "官方记录已确认成功"
                    if target.group:
                        satisfied.add(target.group)
                elif receipt.accepted is False:
                    self.journal.record_rejected(target)
                    states[target.key] = "官方明确拒绝本次提交"
                    # A confirmed rejection can move to the next preference,
                    # but refresh the catalog first to avoid relying on stale seats.
                    refresh_catalog = True
                else:
                    states[target.key] = "官方记录未确认，已停止防止重复提交"
                    return RunReport(states, True)

        return RunReport(states, False)
