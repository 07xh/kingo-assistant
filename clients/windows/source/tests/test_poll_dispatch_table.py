"""Guards for the poll() event-dispatch table: no branch chain may grow back."""
import inspect
import queue
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock

from windows_gui import _POLL_HANDLERS, _STOP_POLL, Application, MonitorEvent

# Every worker event kind the desktop app promises to handle. Removing one of
# these silently drops a worker result (an expired session, a finished run, a
# scheduled-task outcome), so the list is asserted rather than derived.
EXPECTED_KINDS = {
    "tray_restore", "tray_quit", "tray_error", "status",
    "official_probe_done", "official_probe_error", "session_probe",
    "native_login", "ncwu_live_catalog", "known_timetable",
    "ncwu_lesson_detail", "known_year_term", "ncwu_course_plan",
    "ncwu_s202_score", "ncwu_s202_round", "ncwu_s202_results",
    "known_school_results", "native_preparation", "benchmark_done",
    "benchmark_error", "message", "stop_reason", "error",
    "scheduled_waiting", "scheduled_result", "scheduled_blocked",
    "scheduled_cleanup_warning", "scheduled_error", "scheduled_finished",
    "finished",
}

# Only these handlers may end the pump; anything else returning the sentinel
# would stop the Tk timer while background work is still running.
TERMINAL_HANDLERS = {"_on_tray_quit", "_on_finished", "_on_scheduled_finished"}


class PollDispatchTableTests(TestCase):
    def test_every_expected_kind_has_a_callable_handler(self):
        self.assertEqual(set(_POLL_HANDLERS), EXPECTED_KINDS)
        for kind, handler in _POLL_HANDLERS.items():
            self.assertTrue(callable(handler), f"{kind} handler is not callable")

    def test_handlers_are_module_functions_taking_the_app(self):
        for kind, handler in _POLL_HANDLERS.items():
            parameters = list(inspect.signature(handler).parameters)
            self.assertEqual(parameters, ["app", "event"],
                             f"{kind} handler must accept (app, event)")

    def test_poll_body_has_no_per_kind_branch_chain(self):
        source = inspect.getsource(Application.poll)
        self.assertNotIn("event.kind ==", source)
        self.assertLessEqual(len(source.splitlines()), 25)

    def test_only_terminal_handlers_stop_the_pump(self):
        stopping = {
            handler.__name__ for handler in _POLL_HANDLERS.values()
            if "_STOP_POLL" in inspect.getsource(handler)
        }
        self.assertEqual(stopping, TERMINAL_HANDLERS)

    def test_unknown_kind_is_dropped_without_stopping_the_timer(self):
        app = self._fake_app()
        app.events.put(MonitorEvent("kind_from_a_future_worker", "value"))
        Application.poll(app)
        app.after.assert_called_once_with(300, app.poll)
        app.log.assert_not_called()

    def test_message_kind_is_still_logged(self):
        app = self._fake_app()
        app.events.put(MonitorEvent("message", "本人会话验证通过；只读模式。"))
        Application.poll(app)
        app.log.assert_called_once_with("本人会话验证通过；只读模式。")

    def test_sentinel_is_not_a_plain_none(self):
        self.assertIsNot(_STOP_POLL, None)
        self.assertIs(_POLL_HANDLERS["message"](
            self._fake_app(), MonitorEvent("message", "x")), None)

    def _fake_app(self):
        return SimpleNamespace(
            events=queue.Queue(),
            worker=object(),
            _closing=False,
            _tick_portal_appointment=Mock(),
            status=Mock(),
            log=Mock(),
            after=Mock(),
            poll=Mock(),
            destroy=Mock(),
        )


if __name__ == "__main__":
    import unittest

    unittest.main()
