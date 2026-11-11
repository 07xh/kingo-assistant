"""Offline-only enrollment orchestration with a guarded FSM and durable recovery.

No HTTP client, NCWU endpoint or live write adapter is provided here.
A gateway is injected for controlled tests; unverified live writes remain blocked.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass, field
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from typing import Protocol, Sequence

from core.course_models import (
    CourseOffering, CourseTarget, ScheduleSnapshot, offering_matches,
)
from core.enrollment_state import EngineState, EnrollmentFSM, Transition
from core.preflight import inspect_enrollment_schedule


class EnrollmentNotReady(RuntimeError):
    """The authorized enrollment-write contract is not ready."""


# Compatibility names used by the existing offline tests and callers.
Target = CourseTarget
Offering = CourseOffering


@dataclass(frozen=True, slots=True)
class SubmitReceipt:
    """An acknowledgement is not proof of enrollment."""
    accepted: bool | None


@dataclass(frozen=True, slots=True)
class VerificationPolicy:
    """Bounded *read-only* verification. Never applies to write retries."""
    max_reads: int = 3
    interval_seconds: float = 60.0

    def __post_init__(self) -> None:
        if type(self.max_reads) is not int or not 1 <= self.max_reads <= 5:
            raise ValueError("核验次数必须在 1–5 次之间")
        if (type(self.interval_seconds) not in (float, int)
                or not 0 <= self.interval_seconds <= 3600):
            raise ValueError("核验等待时间无效")


@dataclass(frozen=True)
class RunReport:
    states: dict[str, str]
    halted: bool
    transitions: dict[str, tuple[Transition, ...]] = field(default_factory=dict)


class VerifiedGateway(Protocol):
    """Test gateway contract. No real NCWU implementation is included."""
    official_write_verified: bool
    enrollment_semester: str

    async def read_enrollments(self) -> set[tuple[str, str]]: ...
    async def list_offerings(self) -> Sequence[Offering]: ...
    async def read_schedule(self) -> ScheduleSnapshot: ...
    async def submit_once(self, target: Target) -> SubmitReceipt: ...


class AttemptJournal:
    """Durable fence with deterministic (NOT salted) course ID hashes.

    Existing version-1 files with only an attempts array are accepted as
    unresolved, never silently reset. Crash leftovers must be reconciled with
    the official read-only record; do not erase the file to enable a retry.
    """

    LIMIT = 256
    MAX_BYTES = 65536
    OUTCOMES = frozenset(("PENDING", "CONFIRMED", "REJECTED"))

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
        # No account IDs, tokens, URLs or student records are stored.
        return sha256(json.dumps(
            [self.semester, target.course_id, target.class_id],
            ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()

    def _read(self) -> dict:
        self._prepare()
        if not self.path.exists():
            return {"version": 1, "attempts": [], "outcomes": {}}
        if not self.path.is_file() or self.path.stat().st_size > self.MAX_BYTES:
            raise ValueError("本地提交记录异常，拒绝执行")
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (UnicodeError, json.JSONDecodeError, OSError) as exc:
            raise ValueError("提交记录无法读取，拒绝执行") from exc
        if not isinstance(value, dict) or value.get("version") != 1:
            raise ValueError("提交记录版本无效，拒绝执行")
        attempts = value.get("attempts")
        if (not isinstance(attempts, list) or len(attempts) > self.LIMIT
                or len(attempts) != len(set(map(str, attempts)))
                or any(not isinstance(x, str) or len(x) != 64
                       or any(c not in "0123456789abcdef" for c in x)
                       for x in attempts)):
            raise ValueError("提交记录格式无效，拒绝执行")
        outcomes = value.get("outcomes", {})
        if (not isinstance(outcomes, dict) or len(outcomes) > self.LIMIT
                or any(key not in attempts or state not in self.OUTCOMES
                       for key, state in outcomes.items())):
            raise ValueError("提交结果记录无效，拒绝执行")
        return {"version": 1, "attempts": attempts, "outcomes": outcomes}

    def _save(self, data: dict) -> None:
        self._prepare()
        encoded = (json.dumps(data, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":")) + "\n").encode("utf-8")
        if len(encoded) > self.MAX_BYTES:
            raise ValueError("提交状态文件超出容量")
        temp_path = None
        try:
            fd, name = tempfile.mkstemp(prefix=".attempt-", dir=self.path.parent)
            temp_path = Path(name)
            with os.fdopen(fd, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            if self.path.is_symlink():
                raise ValueError("提交状态文件不能为符号链接")
            os.replace(temp_path, self.path)
            # Best effort directory durability on POSIX; Windows os.replace is
            # still atomic, and no exception after a committed replacement.
            if os.name != "nt":
                try:
                    directory_fd = os.open(self.path.parent, os.O_RDONLY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
                except OSError:
                    pass
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    def was_attempted(self, target: Target) -> bool:
        return self._fingerprint(target) in self._read()["attempts"]

    def outcome(self, target: Target) -> str | None:
        data = self._read()
        fingerprint = self._fingerprint(target)
        if fingerprint not in data["attempts"]:
            return None
        return data["outcomes"].get(fingerprint, "PENDING")

    def unresolved(self) -> set[str]:
        data = self._read()
        return {fingerprint for fingerprint in data["attempts"]
                if data["outcomes"].get(fingerprint, "PENDING") == "PENDING"}

    def reserve_before_write(self, target: Target) -> None:
        data = self._read()
        fingerprint = self._fingerprint(target)
        if fingerprint in data["attempts"]:
            raise EnrollmentNotReady("该教学班已存在提交记录，须先核对官方结果")
        if len(data["attempts"]) >= self.LIMIT:
            raise EnrollmentNotReady("本地提交记录达到上限，须人工核对")
        data["attempts"].append(fingerprint)
        data["outcomes"][fingerprint] = "PENDING"
        self._save(data)

    def mark(self, target: Target, outcome: str) -> None:
        if outcome not in self.OUTCOMES:
            raise ValueError("提交结果状态无效")
        data = self._read()
        fingerprint = self._fingerprint(target)
        if fingerprint not in data["attempts"]:
            raise EnrollmentNotReady("不存在对应的持久化提交记录")
        current = data["outcomes"].get(fingerprint, "PENDING")
        if current != "PENDING" and outcome != current:
            raise EnrollmentNotReady("终态不能被改写")
        data["outcomes"][fingerprint] = outcome
        self._save(data)

    @contextmanager
    def exclusive(self):
        """A surviving crash lock fails closed until verified manual recovery."""
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


def _validate_targets(targets: Sequence[Target], semester: str) -> None:
    if not 1 <= len(targets) <= 30:
        raise ValueError("自动选课需要 1–30 条明确目标")
    keys: set[str] = set()
    identities: set[tuple[str, str]] = set()
    for item in targets:
        if not isinstance(item, Target):
            raise ValueError("选课目标类型错误")
        if item.semester and item.semester != semester:
            raise ValueError("选课目标的学期不一致")
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
    """One-account, one-write-per-target offline FSM orchestration.

    Live NCWU writes remain unavailable: only an independently reviewed,
    authorized gateway could ever implement the protocol in this interface.
    """

    def __init__(self, gateway: VerifiedGateway, journal: AttemptJournal,
                 *, user_opted_in: bool = False,
                 institution_permits_write: bool = False,
                 verification_policy: VerificationPolicy | None = None):
        self.gateway = gateway
        self.journal = journal
        self.user_opted_in = user_opted_in
        self.institution_permits_write = institution_permits_write
        self.verification_policy = verification_policy or VerificationPolicy()

    async def _verify(self, fsm: EnrollmentFSM, identity: tuple[str, str],
                      *, max_reads: int | None = None
                      ) -> tuple[bool, set[tuple[str, str]] | None, bool]:
        """Bounded official read-only reconciliation, never a write retry.

        Returns (confirmed, last valid record, read_error_seen). Missing records
        after the bounded window are UNKNOWN unless the gateway explicitly
        rejected the original submission.
        """
        reads = self.verification_policy.max_reads if max_reads is None else max_reads
        latest = None
        had_error = False
        for index in range(reads):
            if index:
                fsm.move(EngineState.VERIFY_WAIT, "await_consistency")
                await asyncio.sleep(self.verification_policy.interval_seconds)
                fsm.move(EngineState.VERIFYING, "verification_read")
            try:
                latest = _read_records(await self.gateway.read_enrollments())
            except Exception:
                had_error = True
                continue
            if identity in latest:
                return True, latest, had_error
        return False, latest, had_error

    async def run(self, targets: Sequence[Target]) -> RunReport:
        targets = list(targets)
        _validate_targets(targets, self.journal.semester)
        if not (self.user_opted_in and self.institution_permits_write
                and getattr(self.gateway, "official_write_verified", False) is True):
            raise EnrollmentNotReady("学校正式选课写入尚未验收，自动提交保持关闭")

        states = {t.key: "未执行" for t in targets}
        fsms = {t.key: EnrollmentFSM() for t in targets}

        def report(halted: bool) -> RunReport:
            return RunReport(states.copy(), halted,
                             {key: fsm.history for key, fsm in fsms.items()})

        with self.journal.exclusive():
            for fsm in fsms.values():
                fsm.move(EngineState.PREFLIGHT, "start")

            try:
                if getattr(self.gateway, "enrollment_semester", None) != self.journal.semester:
                    raise ValueError("已选记录未绑定正确学期")
                enrolled = _read_records(await self.gateway.read_enrollments())
                catalog = list(await self.gateway.list_offerings())
                snapshot = await self.gateway.read_schedule()
                if (not isinstance(snapshot, ScheduleSnapshot)
                        or snapshot.verified is not True
                        or snapshot.semester != self.journal.semester
                        or any(not isinstance(item, Offering) for item in catalog)):
                    raise ValueError("课程目录或学期课表不可信")
                # An earlier unresolved attempt to an *unlisted* target also
                # prevents a new write. A hash alone cannot identify its group.
                pending = self.journal.unresolved()
                active_hashes = {self.journal._fingerprint(t) for t in targets}
                if pending - active_hashes:
                    raise EnrollmentNotReady("另有既往未核验的提交记录")
            except Exception:
                for fsm in fsms.values():
                    fsm.move(EngineState.PROTECTED, "preflight_unknown")
                return RunReport({t.key: "无法确认官方状态" for t in targets}, True,
                                 {key: fsm.history for key, fsm in fsms.items()})

            satisfied = {t.group for t in targets if t.group
                         and (t.course_id, t.class_id) in enrolled}
            occupied = snapshot.slots

            for target in targets:
                fsm = fsms[target.key]
                fsm.move(EngineState.MONITORING, "preflight_ready")
                identity = (target.course_id, target.class_id)

                # An already confirmed enrollment is always checked first.
                if identity in enrolled:
                    if self.journal.was_attempted(target):
                        self.journal.mark(target, "CONFIRMED")
                    states[target.key] = "已在官方选课记录中"
                    fsm.move(EngineState.SUCCESS, "official_record")
                    if target.group:
                        satisfied.add(target.group)
                    continue

                # Crash/restart recovery: read-only verification ONLY.
                if self.journal.was_attempted(target):
                    fsm.move(EngineState.VERIFY_WAIT, "previous_attempt")
                    fsm.move(EngineState.VERIFYING, "reconcile_after_restart")
                    confirmed, latest, _ = await self._verify(fsm, identity)
                    if confirmed:
                        self.journal.mark(target, "CONFIRMED")
                        enrolled = latest or enrolled
                        states[target.key] = "已在官方选课记录中"
                        fsm.move(EngineState.SUCCESS, "recovered_official_record")
                        if target.group:
                            satisfied.add(target.group)
                        continue
                    states[target.key] = "存在既往提交，结果未确认，禁止重发"
                    fsm.move(EngineState.PROTECTED, "prior_attempt_unresolved")
                    return report(True)

                if target.group and target.group in satisfied:
                    states[target.key] = "同组志愿已有成功课程"
                    fsm.move(EngineState.SKIPPED, "group_satisfied")
                    continue
                if any(course_id == target.course_id for course_id, _ in enrolled):
                    states[target.key] = "该课程已有其他教学班，跳过"
                    fsm.move(EngineState.SKIPPED, "other_class_enrolled")
                    continue

                matches = [item for item in catalog
                           if (item.course_id, item.class_id) == identity]
                if len(matches) != 1:
                    states[target.key] = "未找到唯一教学班"
                    fsm.move(EngineState.SKIPPED, "offering_missing_or_duplicate")
                    continue
                offering = matches[0]
                fsm.move(EngineState.TRIGGERED, "unique_offering")
                if not offering_matches(target, offering, self.journal.semester):
                    states[target.key] = "教学班属性或学期未核实"
                    fsm.move(EngineState.PROTECTED, "offering_mismatch")
                    return report(True)
                if offering.remain is None:
                    states[target.key] = "真实余量未知"
                    fsm.move(EngineState.SKIPPED, "seat_count_unknown")
                    continue
                if offering.remain == 0:
                    states[target.key] = "暂无余量"
                    fsm.move(EngineState.SKIPPED, "full")
                    continue

                checked_snapshot = ScheduleSnapshot(self.journal.semester, occupied, True)
                schedule_check = inspect_enrollment_schedule(
                    offering, checked_snapshot, self.journal.semester)
                if not schedule_check.passed:
                    if "重叠" in schedule_check.detail:
                        states[target.key] = "教学时间冲突，跳过"
                        fsm.move(EngineState.SKIPPED, "time_overlap")
                        continue
                    states[target.key] = "课程时间或周次无法核实"
                    fsm.move(EngineState.PROTECTED, "timetable_unknown")
                    return report(True)

                # Never initiate an operation before its durable fence is written.
                try:
                    self.journal.reserve_before_write(target)
                except Exception:
                    states[target.key] = "本机提交记录无法安全写入"
                    fsm.move(EngineState.PROTECTED, "journal_write_failed")
                    return report(True)
                fsm.move(EngineState.SUBMITTING, "fence_reserved")
                receipt = None
                had_submit_error = False
                try:
                    receipt = await self.gateway.submit_once(target)
                    if not isinstance(receipt, SubmitReceipt):
                        had_submit_error = True
                        receipt = None
                except Exception:
                    had_submit_error = True
                # Even a timeout/disconnect/5xx must transition to VERIFYING.
                fsm.move(EngineState.VERIFYING, "submit_outcome_unknown"
                         if had_submit_error else "submit_ack_observed")

                # Only a verified, explicit rejection permits one fallback.
                explicit_rejection = (receipt is not None and
                                      receipt.accepted is False and
                                      not had_submit_error)
                confirmed, latest, read_error = await self._verify(
                    fsm, identity, max_reads=1 if explicit_rejection else None)
                if confirmed:
                    self.journal.mark(target, "CONFIRMED")
                    enrolled = latest or enrolled
                    states[target.key] = "官方记录已确认成功"
                    fsm.move(EngineState.SUCCESS, "official_record_confirmed")
                    if target.group:
                        satisfied.add(target.group)
                    if offering.time_slots is not None:
                        occupied = occupied + offering.time_slots
                elif explicit_rejection and not read_error and latest is not None:
                    self.journal.mark(target, "REJECTED")
                    states[target.key] = "官方明确拒绝本次提交"
                    fsm.move(EngineState.RETRY_WAIT, "official_rejection")
                    fsm.move(EngineState.SKIPPED, "next_choice_allowed")
                    enrolled = latest
                else:
                    # Keep the PENDING fence. Do not retry even after restart.
                    states[target.key] = ("写入结果不确定，已停止防止重复提交"
                                          if had_submit_error or read_error
                                          else "官方记录未确认，已停止防止重复提交")
                    fsm.move(EngineState.PROTECTED, "verification_unresolved")
                    return report(True)
        return report(False)
