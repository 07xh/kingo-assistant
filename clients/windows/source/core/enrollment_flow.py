"""Single V5.1 enrollment flow with explicit V2 FSM transitions (offline only).

This file contains orchestration only. No HTTP requests or school endpoints.
It reuses the original account-scoped HMAC journal and official window gates.
"""
from __future__ import annotations

import asyncio
from typing import Sequence

from core.course_models import ScheduleSnapshot, offering_matches
from core.enrollment import (
    EnrollmentNotReady, Offering, RunReport, SubmitReceipt, Target,
    WindowState, _read_records, _validate_catalog, _validate_targets,
)
from core.enrollment_state import EngineState, EnrollmentFSM
from core.preflight import inspect_enrollment_schedule


async def _verify(engine, fsm: EnrollmentFSM,
                  identity: tuple[str, str], *,
                  reads: int | None = None
                  ) -> tuple[bool, set[tuple[str, str]] | None, bool]:
    """Only bounded authoritative readback; never re-executes submit_once."""
    count = engine.verification_policy.max_reads if reads is None else reads
    latest: set[tuple[str, str]] | None = None
    read_error = False
    for index in range(count):
        if index:
            fsm.move(EngineState.VERIFY_WAIT, "await_consistency")
            await asyncio.sleep(engine.verification_policy.interval_seconds)
            fsm.move(EngineState.VERIFYING, "verification_read")
        try:
            latest = _read_records(await engine.gateway.read_enrollments())
        except Exception:
            read_error = True
            continue
        if identity in latest:
            return True, latest, read_error
    return False, latest, read_error


async def run_enrollment(engine, targets: Sequence[Target]) -> RunReport:
    targets = list(targets)
    _validate_targets(targets, engine.journal.semester)
    engine._gate()

    states = {target.key: "未执行" for target in targets}
    fsms = {target.key: EnrollmentFSM() for target in targets}

    def report(halted: bool) -> RunReport:
        return RunReport(states.copy(), halted,
                         {key: fsm.history for key, fsm in fsms.items()})

    with engine.journal.exclusive():
        for fsm in fsms.values():
            fsm.move(EngineState.PREFLIGHT, "start")
        try:
            window = await engine._window()
            if window is not WindowState.OPEN:
                label = ("学校选课窗口未开放" if window is WindowState.CLOSED
                         else "无法确认学校选课窗口")
                for target in targets:
                    states[target.key] = label
                    fsms[target.key].move(EngineState.PROTECTED, "window_unavailable")
                return report(True)

            # Enrolled records must be explicitly scoped to this semester.
            if getattr(engine.gateway, "enrollment_semester", None) != engine.journal.semester:
                raise ValueError("学期已选记录未经确认")
            enrolled = _read_records(await engine.gateway.read_enrollments())
            catalog = _validate_catalog(await engine.gateway.list_offerings())
            snapshot = await engine.gateway.read_schedule()
            if (not isinstance(snapshot, ScheduleSnapshot)
                    or snapshot.verified is not True
                    or snapshot.semester != engine.journal.semester):
                raise ValueError("学期课表未核验")

            # Do not lose unresolved attempts when a user reorders or removes a
            # target from their current plan.
            journal_doc = engine.journal._read()
            known_hashes = set()
            for target in targets:
                known_hashes.add(engine.journal._fingerprint(target, journal_doc))
                known_hashes.add(engine.journal._legacy_fingerprint(target))
            if engine.journal.unresolved_fingerprints() - known_hashes:
                raise EnrollmentNotReady("存在未列出的既往待核验提交")
        except Exception:
            for target in targets:
                states[target.key] = "无法确认官方状态"
                fsms[target.key].move(EngineState.PROTECTED, "preflight_unknown")
            return report(True)

        # Preserve V5.1 group semantics: an existing enrollment in another
        # teaching class satisfies a preference of the same course.
        satisfied = {target.group for target in targets if target.group
                     and any(course_id == target.course_id
                             for course_id, _ in enrolled)}
        occupied = snapshot.slots
        refresh_catalog = False

        for target in targets:
            fsm = fsms[target.key]
            fsm.move(EngineState.MONITORING, "preflight_ready")
            identity = (target.course_id, target.class_id)
            journal_state = engine.journal.status(target)

            if identity in enrolled:
                if journal_state in ("pending", "rejected", "legacy_pending"):
                    engine.journal.record_confirmed(target)
                states[target.key] = "已在官方选课记录中"
                fsm.move(EngineState.SUCCESS, "official_record")
                if target.group:
                    satisfied.add(target.group)
                continue

            if journal_state in ("pending", "legacy_pending"):
                fsm.move(EngineState.VERIFY_WAIT, "prior_attempt")
                fsm.move(EngineState.VERIFYING, "recovery_read")
                confirmed, latest, _ = await _verify(engine, fsm, identity)
                if confirmed:
                    engine.journal.record_confirmed(target)
                    enrolled = latest or enrolled
                    states[target.key] = "已在官方选课记录中"
                    fsm.move(EngineState.SUCCESS, "recovery_confirmed")
                    if target.group:
                        satisfied.add(target.group)
                    continue
                states[target.key] = "存在未核实提交，禁止自动重发"
                fsm.move(EngineState.PROTECTED, "prior_attempt_unresolved")
                return report(True)

            if journal_state == "confirmed":
                states[target.key] = "历史确认与当前官方记录不一致"
                fsm.move(EngineState.PROTECTED, "confirmed_record_missing")
                return report(True)
            if journal_state == "rejected":
                states[target.key] = "此前已明确拒绝，跳过重复提交"
                fsm.move(EngineState.SKIPPED, "previous_rejection")
                continue

            if target.group and any(course_id == target.course_id
                                    for course_id, _ in enrolled):
                satisfied.add(target.group)
            if target.group and target.group in satisfied:
                states[target.key] = "同组志愿已有成功课程"
                fsm.move(EngineState.SKIPPED, "group_satisfied")
                continue
            if any(course_id == target.course_id for course_id, _ in enrolled):
                states[target.key] = "该课程已有其他教学班，跳过"
                fsm.move(EngineState.SKIPPED, "other_class_enrolled")
                continue

            if refresh_catalog:
                try:
                    catalog = _validate_catalog(await engine.gateway.list_offerings())
                except Exception:
                    states[target.key] = "无法刷新官方目录"
                    fsm.move(EngineState.PROTECTED, "catalog_refresh_failed")
                    return report(True)
                refresh_catalog = False

            matches = [offer for offer in catalog
                       if (offer.course_id, offer.class_id) == identity]
            if len(matches) != 1:
                states[target.key] = "未找到唯一教学班"
                fsm.move(EngineState.SKIPPED, "offering_missing_or_duplicate")
                continue

            offering = matches[0]
            fsm.move(EngineState.TRIGGERED, "unique_offering")
            if not offering_matches(target, offering, engine.journal.semester):
                states[target.key] = "教学班属性或学期未核实"
                fsm.move(EngineState.PROTECTED, "offering_mismatch")
                return report(True)
            if offering.remain is None:
                states[target.key] = "真实余量未知"
                fsm.move(EngineState.SKIPPED, "seat_count_unknown")
                continue
            if offering.remain == 0:
                states[target.key] = "暂无余量"
                fsm.move(EngineState.SKIPPED, "seat_full")
                continue

            # Verified week-aware snapshot is required; missing weeks are
            # unknown. Ordinary user-entered hints remain only advisory.
            schedule_check = inspect_enrollment_schedule(
                offering,
                ScheduleSnapshot(engine.journal.semester, occupied, True),
                engine.journal.semester,
            )
            if not schedule_check.passed:
                if "重叠" in schedule_check.detail:
                    states[target.key] = "教学时间冲突，跳过"
                    fsm.move(EngineState.SKIPPED, "time_overlap")
                    continue
                states[target.key] = "课程时间或周次无法核实"
                fsm.move(EngineState.PROTECTED, "timetable_unknown")
                return report(True)

            # Re-check actual official window immediately before every write.
            try:
                window = await engine._window()
            except Exception:
                window = WindowState.UNKNOWN
            if window is not WindowState.OPEN:
                states[target.key] = ("学校选课窗口已关闭"
                                      if window is WindowState.CLOSED
                                      else "提交前无法确认学校选课窗口")
                fsm.move(EngineState.PROTECTED, "window_closed_before_write")
                return report(True)

            try:
                engine.journal.reserve_before_write(target)
            except Exception:
                states[target.key] = "本机提交记录无法安全写入"
                fsm.move(EngineState.PROTECTED, "journal_reservation_failed")
                return report(True)

            fsm.move(EngineState.SUBMITTING, "hmac_intent_persisted")
            receipt = None
            submit_error = False
            try:
                receipt = await engine.gateway.submit_once(target)
                if not isinstance(receipt, SubmitReceipt):
                    receipt = None
                    submit_error = True
            except Exception:
                submit_error = True
            fsm.move(EngineState.VERIFYING, "submit_outcome_unknown"
                     if submit_error else "receipt_observed")

            explicit_rejection = (
                receipt is not None and receipt.accepted is False
                and not submit_error
            )
            confirmed, latest, read_error = await _verify(
                engine, fsm, identity, reads=1 if explicit_rejection else None)
            if confirmed:
                engine.journal.record_confirmed(target)
                enrolled = latest or enrolled
                states[target.key] = "官方记录已确认成功"
                fsm.move(EngineState.SUCCESS, "official_record_confirmed")
                if target.group:
                    satisfied.add(target.group)
                if offering.time_slots is not None:
                    occupied = occupied + offering.time_slots
            elif explicit_rejection and not read_error and latest is not None:
                engine.journal.record_rejected(target)
                states[target.key] = "官方明确拒绝本次提交"
                fsm.move(EngineState.RETRY_WAIT, "official_rejection")
                fsm.move(EngineState.SKIPPED, "next_preference")
                refresh_catalog = True
                enrolled = latest
            else:
                states[target.key] = ("写入结果不确定，已停止防止重复提交"
                                      if submit_error or read_error
                                      else "官方记录未确认，已停止防止重复提交")
                fsm.move(EngineState.PROTECTED, "verification_unresolved")
                return report(True)

    return report(False)
