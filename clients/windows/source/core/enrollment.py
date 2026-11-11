"""Offline-testable automatic enrollment orchestration.

This module makes NO HTTP requests. A school-specific adapter must be
separately implemented and verified against officially permitted read/write
behavior before it can be used. No adapter is supplied in this repository.

One write per precise course+class target; never auto-retry an uncertain write.
A durable intent journal is reserved BEFORE each submission, so a restart
cannot silently repeat an ambiguous operation.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from typing import Protocol, Sequence


class EnrollmentNotReady(RuntimeError):
    """Live submission is disabled until explicitly authorized and integrated."""


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
    """Acknowledgement alone is NOT proof of enrollment."""
    accepted: bool | None


@dataclass(frozen=True)
class RunReport:
    states: dict[str, str]
    halted: bool


class VerifiedGateway(Protocol):
    """Injected adapter contract. No NCWU implementation is provided."""

    # A checked capability in the adapter, not a user-supplied arbitrary URL.
    official_write_verified: bool

    async def read_enrollments(self) -> set[tuple[str, str]]: ...
    async def list_offerings(self) -> Sequence[Offering]: ...
    async def submit_once(self, target: Target) -> SubmitReceipt: ...


class AttemptJournal:
    """Fail-closed local persistence containing only salted course fingerprints.

    A surviving lock after a crash requires manual reconciliation. Never
    remove the journal just to retry an uncertain submission.
    """

    def __init__(self, path: Path, semester: str):
        self.path = Path(path)
        if not isinstance(semester, str) or not semester.strip():
            raise ValueError("学期不能为空")
        self.semester = semester.strip()
        self.lock_path = self.path.with_name(self.path.name + ".lock")

    def _prepare(self) -> None:
        if self.path.parent.is_symlink() or self.path.is_symlink():
            raise ValueError("不允许使用符号链接保存选课状态")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.parent.is_symlink() or self.path.is_symlink():
            raise ValueError("选课状态路径不安全")

    def _fingerprint(self, target: Target) -> str:
        # Do not save a student identifier, access token, URL or response body.
        return sha256(json.dumps(
            [self.semester, target.course_id, target.class_id],
            ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")).hexdigest()

    def _read(self) -> dict:
        self._prepare()
        if not self.path.exists():
            return {"version": 1, "attempts": []}
        if not self.path.is_file() or self.path.stat().st_size > 65536:
            raise ValueError("本地提交记录异常，拒绝执行")
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError("提交记录无法读取，拒绝执行") from exc
        if (not isinstance(value, dict) or value.get("version") != 1
                or not isinstance(value.get("attempts"), list)
                or any(not isinstance(x, str) or len(x) != 64
                       for x in value["attempts"])):
            raise ValueError("提交记录格式无效，拒绝执行")
        return value

    def was_attempted(self, target: Target) -> bool:
        return self._fingerprint(target) in self._read()["attempts"]

    def reserve_before_write(self, target: Target) -> None:
        data = self._read()
        fingerprint = self._fingerprint(target)
        if fingerprint in data["attempts"]:
            raise EnrollmentNotReady("该教学班已存在提交记录，须先核对官方结果")
        if len(data["attempts"]) >= 256:
            raise EnrollmentNotReady("本地提交记录达到上限，须人工核对")
        data["attempts"].append(fingerprint)
        encoded = (json.dumps(data, ensure_ascii=False) + "\n").encode("utf-8")
        temp_path = None
        try:
            fd, name = tempfile.mkstemp(prefix=".attempt-", dir=self.path.parent)
            temp_path = Path(name)
            with os.fdopen(fd, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, self.path)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    @contextmanager
    def exclusive(self):
        """Prevent simultaneous local runs; a stale lock fails closed."""
        self._prepare()
        try:
            fd = os.open(self.lock_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError as exc:
            raise EnrollmentNotReady("已有选课任务或上次任务未安全退出") from exc
        try:
            os.close(fd)
            yield
        finally:
            self.lock_path.unlink(missing_ok=True)


def _validate_targets(targets: Sequence[Target]) -> None:
    if not 1 <= len(targets) <= 30:
        raise ValueError("自动选课需要 1–30 条明确目标")
    keys = set()
    identities = set()
    for item in targets:
        if not isinstance(item, Target):
            raise ValueError("选课目标类型错误")
        fields = (item.key, item.course_id, item.class_id)
        if any(not isinstance(x, str) or not x.strip() or len(x) > 100
               or any(ord(ch) < 32 for ch in x) for x in fields):
            raise ValueError("必须提供明确有效的课程代码和教学班编号")
        if not isinstance(item.group, str) or len(item.group) > 100:
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
           or any(not isinstance(x, str) or not x.strip() for x in item)
           for item in records):
        raise ValueError("官方选课结果无法可靠匹配")
    return set(records)


class EnrollmentEngine:
    """Single-worker priority and fallback state machine.

    The engine does not schedule, circumvent controls, or implement an HTTP
    enrollment write. Only a separately verified adapter may perform one.
    """

    def __init__(self, gateway: VerifiedGateway, journal: AttemptJournal,
                 *, user_opted_in: bool = False, institution_permits_write: bool = False):
        self.gateway = gateway
        self.journal = journal
        self.user_opted_in = user_opted_in
        self.institution_permits_write = institution_permits_write

    async def run(self, targets: Sequence[Target]) -> RunReport:
        targets = list(targets)
        _validate_targets(targets)
        if not (self.user_opted_in and self.institution_permits_write
                and getattr(self.gateway, "official_write_verified", False) is True):
            raise EnrollmentNotReady("学校正式选课写入尚未验收，自动提交保持关闭")
        states = {t.key: "未执行" for t in targets}
        with self.journal.exclusive():
            # Read the official result before any write, including after restart.
            try:
                enrolled = _read_records(await self.gateway.read_enrollments())
                catalog = list(await self.gateway.list_offerings())
                if any(not isinstance(x, Offering) or
                       (x.remain is not None and
                        (type(x.remain) is not int or x.remain < 0))
                       for x in catalog):
                    raise ValueError("课程余量响应结构不可信")
            except Exception:
                return RunReport({t.key: "无法确认官方状态" for t in targets}, True)

            # Alternatives in a group are satisfied by one confirmed choice.
            satisfied = {t.group for t in targets if t.group
                         and (t.course_id, t.class_id) in enrolled}
            for target in targets:
                identity = (target.course_id, target.class_id)
                if identity in enrolled:
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
                if self.journal.was_attempted(target):
                    states[target.key] = "存在既往提交，需核对官方结果"
                    return RunReport(states, True)
                matches = [offer for offer in catalog
                           if (offer.course_id, offer.class_id) == identity]
                if len(matches) != 1:
                    states[target.key] = "未找到唯一教学班"
                    continue
                if matches[0].remain is None:
                    states[target.key] = "真实余量未知"
                    continue
                if matches[0].remain == 0:
                    states[target.key] = "暂无余量"
                    continue

                # Commit to an idempotency fence BEFORE potentially sending data.
                self.journal.reserve_before_write(target)
                try:
                    receipt = await self.gateway.submit_once(target)
                    if not isinstance(receipt, SubmitReceipt):
                        raise ValueError("写入确认响应结构异常")
                    # A successful HTTP response is NOT confirmation.
                    enrolled = _read_records(await self.gateway.read_enrollments())
                except Exception:
                    states[target.key] = "写入结果不确定，已停止防止重复提交"
                    return RunReport(states, True)
                if identity in enrolled:
                    states[target.key] = "官方记录已确认成功"
                    if target.group:
                        satisfied.add(target.group)
                elif receipt.accepted is False:
                    states[target.key] = "官方明确拒绝本次提交"
                    # An explicitly rejected alternative can fall back, once.
                else:
                    states[target.key] = "官方记录未确认，已停止防止重复提交"
                    return RunReport(states, True)
        return RunReport(states, False)
