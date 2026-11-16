"""GUI read-worker deduplication preserves status updates and threading."""
import queue
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import AsyncMock, Mock, patch

from core.ncwu_s202_round import S202RoundStatus
from windows_gui import _ncwu_read_busy, _start_ncwu_read_worker


def fake_app():
    return SimpleNamespace(
        worker=None, scheduled_worker=None, _closing=False,
        _native_login_in_progress=False,
        _native_preparation_in_progress=False,
        _session_check_in_progress=False,
        _s202_round_probe_in_progress=False,
        _course_plan_in_progress=False,
        _live_polling=False,
        events=queue.Queue(),
    )


class NativeReadWorkerLifecycleTests(TestCase):
    def test_common_guards_preserve_running_and_optional_polling(self):
        app = fake_app()
        self.assertFalse(_ncwu_read_busy(app))
        app._s202_round_probe_in_progress = True
        self.assertTrue(_ncwu_read_busy(app))
        app._s202_round_probe_in_progress = False
        app._live_polling = True
        self.assertFalse(_ncwu_read_busy(app))
        self.assertTrue(_ncwu_read_busy(app, include_polling=True))
        app._closing = True
        self.assertTrue(_ncwu_read_busy(app))

    def test_worker_posts_typed_result_without_accessing_tk_from_thread(self):
        app = fake_app()
        status = Mock()
        button = Mock()
        read = AsyncMock(return_value=S202RoundStatus("observed", "closed"))
        with patch("windows_gui.threading.Thread") as thread:
            _start_ncwu_read_worker(
                app, config={"base_url": "https://jwmis.ncwu.edu.cn"},
                cookie="JSESSIONID=EXE_TEST",
                read_call=read, fallback_factory=S202RoundStatus,
                flag="_s202_round_probe_in_progress", button=button,
                status_var=status, running_message="running",
                failure_message="failed", event_kind="ncwu_s202_round",
                thread_name="KingoS202RoundProbe",
            )
            self.assertTrue(app._s202_round_probe_in_progress)
            button.configure.assert_called_once_with(state="disabled")
            status.set.assert_called_once_with("running")
            thread.assert_called_once()
            self.assertEqual(thread.call_args.kwargs["name"], "KingoS202RoundProbe")
            thread.call_args.kwargs["target"]()
        read.assert_awaited_once_with(
            {"base_url": "https://jwmis.ncwu.edu.cn"},
            session_cookie="JSESSIONID=EXE_TEST",
        )
        event = app.events.get_nowait()
        self.assertEqual(event.kind, "ncwu_s202_round")
        self.assertEqual(event.value.window, "closed")

    def test_start_failure_restores_button_flag_and_status(self):
        app = fake_app()
        status, button = Mock(), Mock()
        with patch("windows_gui.threading.Thread",
                   side_effect=RuntimeError("worker blocked")):
            _start_ncwu_read_worker(
                app, config={}, cookie="JSESSIONID=LOCAL",
                read_call=AsyncMock(),
                fallback_factory=S202RoundStatus,
                flag="_s202_round_probe_in_progress", button=button,
                status_var=status, running_message="running",
                failure_message="thread unavailable",
                event_kind="ncwu_s202_round", thread_name="KingoTest",
            )
        self.assertFalse(app._s202_round_probe_in_progress)
        self.assertEqual(
            [c.kwargs for c in button.configure.call_args_list],
            [{"state": "disabled"}, {"state": "normal"}],
        )
        self.assertEqual(
            [c.args[0] for c in status.set.call_args_list],
            ["running", "thread unavailable"],
        )
        self.assertTrue(app.events.empty())

    def test_read_failure_becomes_safe_unavailable_without_raw_errors(self):
        app = fake_app()
        status, button = Mock(), Mock()
        read = AsyncMock(side_effect=RuntimeError(
            "PRIVATE_SECRET_REQUEST_BODY_MUST_NOT_LEAK"))
        with patch("windows_gui.threading.Thread") as thread:
            _start_ncwu_read_worker(
                app, config={}, cookie="JSESSIONID=LOCAL",
                read_call=read, fallback_factory=S202RoundStatus,
                flag="_s202_round_probe_in_progress", button=button,
                status_var=status, running_message="running",
                failure_message="thread unavailable",
                event_kind="ncwu_s202_round", thread_name="KingoTest",
            )
            thread.call_args.kwargs["target"]()
        event = app.events.get_nowait()
        self.assertEqual(event.value.code, "unavailable")
        self.assertNotIn("PRIVATE_SECRET", repr(event))


if __name__ == "__main__":
    import unittest
    unittest.main()
