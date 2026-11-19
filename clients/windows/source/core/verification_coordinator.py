"""Detached, read-only official result verification for a final enrollment target.

No second write is possible here. The durable 'pending' intent stays fenced
while the task runs, including if the process stops before it completes.
The caller MUST await finish() before closing the owned session/client.
"""
from __future__ import annotations

import asyncio

from core.enrollment import RunReport
from core.enrollment_state import EngineState


class VerificationCoordinator:
    """One task for a submitted terminal target; no speculative fallback."""

    def __init__(self, engine, target, fsm, states: dict, fsms: dict,
                 *, explicit_rejection: bool, submit_error: bool):
        self.engine = engine
        self.target = target
        self.fsm = fsm
        self.states = dict(states)
        self.fsms = fsms
        self.explicit_rejection = explicit_rejection
        self.submit_error = submit_error
        self.task: asyncio.Task | None = None

    def start(self) -> None:
        if self.task is not None:
            raise RuntimeError("核验任务已启动，不可重复创建")
        self.task = asyncio.create_task(self._run(), name="KingoOfficialReadback")

    def _report(self, halted: bool) -> RunReport:
        return RunReport(
            dict(self.states), halted,
            {key: fsm.history for key, fsm in self.fsms.items()},
        )

    async def _run(self) -> RunReport:
        # Import at runtime to keep the existing FSM helper as the single
        # authoritative verification implementation and avoid circular import.
        from core.enrollment_flow import _verify
        identity = (self.target.course_id, self.target.class_id)
        latest = None
        read_error = True
        confirmed = False
        try:
            confirmed, latest, read_error = await _verify(
                self.engine, self.fsm, identity,
                reads=1 if self.explicit_rejection else None,
            )
        except Exception:
            # Unknown stays pending: neither retry nor claim failure.
            read_error = True

        if confirmed:
            try:
                with self.engine.journal.exclusive():
                    self.engine.journal.record_confirmed(self.target)
                self.states[self.target.key] = "官方记录已确认成功"
                self.fsm.move(EngineState.SUCCESS, "official_record_confirmed")
                return self._report(False)
            except Exception:
                # We may have seen a positive response, but failure to persist
                # prevents claiming a durable final result.
                self.states[self.target.key] = "官方结果已返回，但本机确认记录未能保存"
                self.fsm.move(EngineState.PROTECTED, "journal_confirm_failed")
                return self._report(True)

        if self.explicit_rejection and not read_error and latest is not None:
            try:
                with self.engine.journal.exclusive():
                    self.engine.journal.record_rejected(self.target)
                self.states[self.target.key] = "官方明确拒绝本次提交"
                self.fsm.move(EngineState.RETRY_WAIT, "official_rejection")
                self.fsm.move(EngineState.SKIPPED, "next_preference")
                return self._report(False)
            except Exception:
                self.states[self.target.key] = "拒绝结果无法安全持久化，待确认"
                self.fsm.move(EngineState.PROTECTED, "journal_reject_failed")
                return self._report(True)

        self.states[self.target.key] = (
            "写入结果不确定，已停止防止重复提交"
            if self.submit_error or read_error
            else "官方记录未确认，已停止防止重复提交"
        )
        self.fsm.move(EngineState.PROTECTED, "verification_unresolved")
        return self._report(True)

    async def finish(self) -> RunReport:
        if self.task is None:
            raise RuntimeError("独立核验任务尚未启动")
        return await asyncio.shield(self.task)
