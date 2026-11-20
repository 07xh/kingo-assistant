"""Offline tests for the concurrent multi-account runner.

No sockets: every session is a stub that records concurrency, raises a
classified failure on demand, or sleeps to burn the deadline. What is asserted
is the policy -- retry, breaker, challenge halt, deadline, session closing --
because that policy is what decides whether a burst wins a seat or locks an
account.
"""
import asyncio
import tempfile
import unittest
from pathlib import Path

from core.multi_account_plan import AccountPlan, AccountTarget
from core.multi_account_run import (
    FailureKind, MultiAccountRunner, RunError, classify_failure, run_summary,
)
from core.rush_profile import RushProfile


class HttpFailure(Exception):
    def __init__(self, message: str, status_code: int | None = None,
                 retry_after: float | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


def profile(**overrides) -> RushProfile:
    base = {
        "preset": "test",
        "concurrency": 3,
        "min_interval": 0.05,
        "window_seconds": 1.0,
        "window_requests": 1000,
        "stagger_ms": 0,
        "max_attempts": 10,
        "backoff_base": 0.001,
        "backoff_cap": 0.002,
        "backoff_jitter": 0.0,
        "circuit_breaker": 3,
        "deadline_seconds": 10.0,
        "retry_after_cap": 5.0,
    }
    base.update(overrides)
    return RushProfile(**base)


class RecordingSession:
    def __init__(self, name: str, behaviour=None, *, delay: float = 0.0) -> None:
        self.name = name
        self.behaviour = behaviour
        self.delay = delay
        self.calls: list[AccountTarget] = []
        self.closed = False

    async def submit(self, target):
        self.calls.append(target)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.behaviour is not None:
            result = self.behaviour(target, len(self.calls))
            if isinstance(result, BaseException):
                raise result
            if asyncio.iscoroutine(result):
                return await result
            return result
        return {"accepted": True, "account": self.name, "target": target.key}

    async def aclose(self) -> None:
        self.closed = True


class RunnerCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def plan(self, name: str, targets: int = 2, index: int = 0) -> AccountPlan:
        return AccountPlan(
            name=name,
            database=self.root / f"{name}-{index}.sqlite",
            origin="https://jw.example.edu.cn",
            targets=tuple(
                AccountTarget(course_id=f"C{i}", class_id=f"K{i}")
                for i in range(1, targets + 1)),
            windows=((60.0, 120),),
            policy_source="测试用策略",
        )

    async def test_all_targets_submitted_and_sessions_closed(self):
        sessions: dict[str, RecordingSession] = {}

        def factory(plan: AccountPlan) -> RecordingSession:
            session = RecordingSession(plan.name, delay=0.02)
            sessions[plan.name] = session
            return session

        plans = [self.plan("a", 2), self.plan("b", 2), self.plan("c", 2)]
        report = await MultiAccountRunner(
            plans=plans, session_factory=factory, profile=profile()).run()

        self.assertEqual(report.submitted, 6)
        self.assertEqual(report.failed, 0)
        self.assertTrue(all(session.closed for session in sessions.values()))
        self.assertLessEqual(report.budget["peak_inflight"], 3)
        self.assertGreaterEqual(report.budget["peak_inflight"], 2)
        self.assertEqual(report.elapsed > 0, True)

    async def test_duplicate_is_held_not_retried(self):
        def behaviour(target, attempt):
            return HttpFailure("已经选过该课程", status_code=200)

        def factory(plan):
            return RecordingSession(plan.name, behaviour)

        report = await MultiAccountRunner(
            plans=[self.plan("a", 1)], session_factory=factory,
            profile=profile()).run()
        outcome = report.accounts[0]
        self.assertEqual(outcome.status, "already_held")
        self.assertEqual(outcome.held, 1)
        self.assertEqual(outcome.targets[0].attempts, 1)

    async def test_challenge_halts_the_account_and_skips_the_rest(self):
        def behaviour(target, attempt):
            return HttpFailure("请拖动滑块完成验证", status_code=200)

        def factory(plan):
            return RecordingSession(plan.name, behaviour)

        report = await MultiAccountRunner(
            plans=[self.plan("a", 3)], session_factory=factory,
            profile=profile()).run()
        outcome = report.accounts[0]
        self.assertEqual(outcome.status, "halted")
        self.assertIn("人机校验", outcome.halted_reason)
        self.assertEqual([t.status for t in outcome.targets],
                         ["halted", "skipped_halted", "skipped_halted"])

    async def test_retry_after_is_honoured_then_the_target_succeeds(self):
        def behaviour(target, attempt):
            if attempt == 1:
                return HttpFailure("429 Too Many Requests", status_code=429,
                                   retry_after=0.05)
            return {"accepted": True}

        def factory(plan):
            return RecordingSession(plan.name, behaviour)

        report = await MultiAccountRunner(
            plans=[self.plan("a", 1)], session_factory=factory,
            profile=profile()).run()
        outcome = report.accounts[0]
        self.assertEqual(outcome.status, "submitted")
        self.assertEqual(outcome.targets[0].attempts, 2)
        self.assertGreaterEqual(outcome.targets[0].attempts, 2)

    async def test_persistent_transient_failure_trips_the_breaker(self):
        def behaviour(target, attempt):
            return HttpFailure("503 Service Unavailable", status_code=503)

        def factory(plan):
            return RecordingSession(plan.name, behaviour)

        report = await MultiAccountRunner(
            plans=[self.plan("a", 3)], session_factory=factory,
            profile=profile(circuit_breaker=3, max_attempts=20)).run()
        outcome = report.accounts[0]
        self.assertEqual(outcome.status, "halted")
        self.assertIn("熔断", outcome.halted_reason)
        self.assertEqual(outcome.targets[0].attempts, 3)

    async def test_fatal_failure_stops_the_target_but_keeps_the_account(self):
        def behaviour(target, attempt):
            if target.class_id == "K1":
                return HttpFailure("未知状态", status_code=200)
            return {"accepted": True}

        def factory(plan):
            return RecordingSession(plan.name, behaviour)

        report = await MultiAccountRunner(
            plans=[self.plan("a", 2)], session_factory=factory,
            profile=profile()).run()
        outcome = report.accounts[0]
        self.assertEqual(outcome.targets[0].status, "failed")
        self.assertEqual(outcome.targets[0].attempts, 1)
        self.assertEqual(outcome.targets[1].status, "submitted")
        self.assertEqual(outcome.status, "partial")

    async def test_deadline_skips_remaining_targets(self):
        def factory(plan):
            return RecordingSession(plan.name, delay=0.2)

        report = await MultiAccountRunner(
            plans=[self.plan("a", 2)], session_factory=factory,
            profile=profile(), deadline_seconds=0.15).run()
        statuses = [t.status for t in report.accounts[0].targets]
        self.assertEqual(statuses[0], "submitted")
        self.assertEqual(statuses[1], "skipped_deadline")

    async def test_missing_session_factory_marks_the_account_without_contact(self):
        def factory(plan):
            raise RunError("没有会话")

        report = await MultiAccountRunner(
            plans=[self.plan("a", 2)], session_factory=factory,
            profile=profile()).run()
        outcome = report.accounts[0]
        self.assertEqual(outcome.status, "no_session")
        self.assertEqual(outcome.failed, 2)

    async def test_async_factory_is_awaited(self):
        session = RecordingSession("a")

        async def factory(plan):
            return session

        report = await MultiAccountRunner(
            plans=[self.plan("a", 1)], session_factory=factory,
            profile=profile()).run()
        self.assertEqual(report.submitted, 1)
        self.assertTrue(session.closed)

    async def test_cancelling_the_run_closes_every_session(self):
        session = RecordingSession("a", delay=5.0)

        def factory(plan):
            return session

        runner = MultiAccountRunner(plans=[self.plan("a", 1)],
                                    session_factory=factory, profile=profile())
        task = asyncio.create_task(runner.run())
        await asyncio.sleep(0.05)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(session.closed)

    async def test_two_accounts_run_against_separate_databases_only(self):
        def factory(plan):
            return RecordingSession(plan.name)

        plans = [self.plan("a", 1), self.plan("b", 1)]
        report = await MultiAccountRunner(
            plans=plans, session_factory=factory, profile=profile()).run()
        self.assertEqual(report.submitted, 2)
        summary = run_summary(report)
        self.assertIn("a:", summary)
        self.assertIn("b:", summary)


class ValidationTests(unittest.TestCase):
    def make(self, name: str, database: str) -> AccountPlan:
        return AccountPlan(name=name, database=Path(database),
                           origin="https://jw.example.edu.cn",
                           targets=(AccountTarget("C1", "K1"),))

    def test_shared_database_is_refused(self):
        plans = [self.make("a", "C:/tmp/shared.sqlite"),
                 self.make("b", "C:/tmp/shared.sqlite")]
        with self.assertRaises(RunError):
            MultiAccountRunner(plans=plans, session_factory=lambda p: None,
                               profile=profile())

    def test_duplicate_names_are_refused(self):
        plans = [self.make("a", "C:/tmp/a.sqlite"),
                 self.make("a", "C:/tmp/b.sqlite")]
        with self.assertRaises(RunError):
            MultiAccountRunner(plans=plans, session_factory=lambda p: None,
                               profile=profile())

    def test_empty_plans_are_refused(self):
        with self.assertRaises(RunError):
            MultiAccountRunner(plans=[], session_factory=lambda p: None,
                               profile=profile())

    def test_non_callable_factory_is_refused(self):
        with self.assertRaises(RunError):
            MultiAccountRunner(plans=[self.make("a", "C:/tmp/a.sqlite")],
                               session_factory="nope", profile=profile())


class ClassificationTests(unittest.TestCase):
    def test_challenge_wins_over_everything_else(self):
        kind, _ = classify_failure(HttpFailure("429 请完成验证码", status_code=429))
        self.assertIs(kind, FailureKind.CHALLENGE)

    def test_duplicate_marker(self):
        kind, _ = classify_failure(Exception("该教学班已存在选课记录"))
        self.assertIs(kind, FailureKind.DUPLICATE)

    def test_status_429_is_throttled_and_carries_a_default_delay(self):
        kind, delay = classify_failure(HttpFailure("slow down", status_code=429))
        self.assertIs(kind, FailureKind.THROTTLED)
        self.assertEqual(delay, 1.0)

    def test_server_error_is_transient(self):
        kind, _ = classify_failure(HttpFailure("boom", status_code=502))
        self.assertIs(kind, FailureKind.TRANSIENT)

    def test_unknown_failure_is_fatal(self):
        kind, _ = classify_failure(ValueError("状态不一致"))
        self.assertIs(kind, FailureKind.FATAL)

    def test_timeout_text_is_transient(self):
        kind, _ = classify_failure(TimeoutError("请求超时"))
        self.assertIs(kind, FailureKind.TRANSIENT)


if __name__ == "__main__":
    unittest.main()
