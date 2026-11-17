"""Keep S202 live button, opt-in polling and native read events unchanged."""
import queue
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import AsyncMock, Mock, patch

from core.ncwu_live_elective import LiveElectiveSnapshot
from windows_gui import Application, MonitorEvent


def app_fixture():
    return SimpleNamespace(
        worker=None, scheduled_worker=None, _closing=False,
        _native_login_in_progress=False,
        _native_preparation_in_progress=False,
        _session_check_in_progress=False,
        _native_session_cookie="JSESSIONID=LOCAL_SYNTHETIC",
        _native_session_origin="jwmis.ncwu.edu.cn",
        _live_catalog_in_progress=False, _live_polling=False,
        _live_poll_timer=None, live_poll_button=Mock(),
        live_catalog_status=Mock(), live_catalog_button=Mock(),
        native_login_status=Mock(), status=Mock(), log=Mock(),
        path=SimpleNamespace(get=lambda: "config.yaml"),
        events=queue.Queue(), _tick_portal_appointment=Mock(),
        after=Mock(), poll=Mock(), check_live_catalog=Mock(),
        after_cancel=Mock(), show_live_catalog_rows=Mock(),
    )


class LiveCatalogGuiRefactorTests(TestCase):
    def test_manual_live_read_reuses_native_worker_with_original_name(self):
        app = app_fixture()
        cfg = {"base_url": "https://jwmis.ncwu.edu.cn"}
        async_result = LiveElectiveSnapshot("catalog_read", "catalog", "closed")
        with patch("windows_gui.read_config", return_value=cfg), \
             patch("windows_gui.threading.Thread") as thread, \
             patch("windows_gui.live_s202_snapshot", new_callable=AsyncMock,
                   return_value=async_result) as read:
            Application.check_live_catalog(app)
            thread.assert_called_once()
            self.assertEqual(
                thread.call_args.kwargs["name"], "KingoLiveS202Query")
            thread.call_args.kwargs["target"]()
        read.assert_awaited_once_with(
            cfg, session_cookie="JSESSIONID=LOCAL_SYNTHETIC")
        event = app.events.get_nowait()
        self.assertEqual(event.kind, "ncwu_live_catalog")
        self.assertIs(event.value, async_result)
        app.live_catalog_button.configure.assert_called_once_with(
            state="disabled")

    def test_opt_in_polling_does_not_block_next_live_read(self):
        app = app_fixture()
        app._live_polling = True
        with patch("windows_gui.read_config", return_value={
            "base_url": "https://jwmis.ncwu.edu.cn"}), \
             patch("windows_gui.threading.Thread") as thread:
            Application.check_live_catalog(app)
        thread.assert_called_once()

    def test_catalog_result_reuses_original_60_second_next_cycle(self):
        app = app_fixture()
        app._live_polling = True
        result = LiveElectiveSnapshot("catalog_read", "catalog", "closed")
        app.events.put(MonitorEvent("ncwu_live_catalog", result))
        Application.poll(app)
        app.show_live_catalog_rows.assert_called_once_with(result)
        self.assertFalse(app._live_catalog_in_progress)
        self.assertTrue(any(
            call.args == (60_000, app.check_live_catalog)
            for call in app.after.call_args_list
        ))
        app.live_catalog_button.configure.assert_called_once_with(
            state="normal")

    def test_expired_read_stops_polling_and_clears_current_session(self):
        app = app_fixture()
        app._live_polling = True
        # Use the true method so _live_polling is updated, rather than
        # a Mock that would hide a double-stop bug in the event handler.
        app.stop_live_polling = lambda: Application.stop_live_polling(app)
        app.events.put(MonitorEvent(
            "ncwu_live_catalog", LiveElectiveSnapshot("expired", "page")))
        Application.poll(app)
        self.assertIsNone(app._native_session_cookie)
        self.assertIsNone(app._native_session_origin)
        self.assertFalse(app._live_polling)
        app.live_poll_button.configure.assert_called_once_with(
            text="开始自动刷新（≥60秒）")
        app.show_live_catalog_rows.assert_not_called()
        self.assertFalse(any(
            call.args and call.args[0] == 60_000
            for call in app.after.call_args_list
        ))


if __name__ == "__main__":
    import unittest
    unittest.main()
