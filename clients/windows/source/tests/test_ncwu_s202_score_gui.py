"""Headless Windows wiring tests for S202 score and local HAR audit."""
import json
import queue
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import AsyncMock, Mock, patch

from core.ncwu_s202_score import PersonalScoreCount
from windows_gui import Application


class S202ScoreGuiTests(TestCase):
    def setUp(self):
        self.app = SimpleNamespace(
            worker=None, scheduled_worker=None, _closing=False,
            _native_login_in_progress=False,
            _native_preparation_in_progress=False,
            _session_check_in_progress=False,
            _native_session_cookie=None, _native_session_origin=None,
            path=SimpleNamespace(get=lambda: "config.yaml"),
            s202_score_button=Mock(), s202_score_status=Mock(),
            events=queue.Queue(), log=Mock(),
        )

    def test_without_login_makes_zero_requests(self):
        with patch("windows_gui.read_config") as load, \
             patch("windows_gui.threading.Thread") as worker:
            Application.check_personal_s202_score(self.app)
        load.assert_not_called()
        worker.assert_not_called()

    def test_native_score_probe_uses_same_session_without_browser(self):
        cfg = {"base_url": "https://jwmis.ncwu.edu.cn"}
        self.app._native_session_cookie = "JSESSIONID=LOCAL_ONLY"
        self.app._native_session_origin = "jwmis.ncwu.edu.cn"
        with patch("windows_gui.read_config", return_value=cfg), \
             patch("windows_gui.threading.Thread") as thread, \
             patch("windows_gui.read_personal_s202_score",
                   new_callable=AsyncMock,
                   return_value=PersonalScoreCount(
                       "count_observed", 2, True)) as read, \
             patch("windows_gui.webbrowser.open") as browser:
            Application.check_personal_s202_score(self.app)
            thread.assert_called_once()
            self.assertEqual(
                thread.call_args.kwargs["name"], "KingoS202ScoreRead")
            thread.call_args.kwargs["target"]()
            browser.assert_not_called()
        read.assert_awaited_once_with(
            cfg, session_cookie="JSESSIONID=LOCAL_ONLY")
        evt = self.app.events.get_nowait()
        self.assertEqual(evt.kind, "ncwu_s202_score")
        self.assertEqual(evt.value.selected_count, 2)

    def test_local_contract_inspection_never_sends_har_credentials(self):
        with TemporaryDirectory() as td:
            path = Path(td) / "personal.har"
            path.write_text(json.dumps({"log": {"entries": [{
                "request": {
                    "method": "POST",
                    "url": ("https://jwmis.ncwu.edu.cn"
                            "/hsjw/STU_DynamicInitDataAction.do"),
                    "postData": {
                        "mimeType": "application/x-www-form-urlencoded",
                        "text": "xh=SENSITIVE_PERSONAL_ID&xn=2026",
                    },
                },
                "response": {
                    "status": 200,
                    "content": {"text":
                        '{"status":"200","result":"SENSITIVE_SESSION"}'},
                },
            }]}}), encoding="utf-8")
            with patch("windows_gui.filedialog.askopenfilename",
                       return_value=str(path)), \
                 patch("windows_gui.messagebox.showinfo") as info, \
                 patch("windows_gui.threading.Thread") as worker, \
                 patch("windows_gui.webbrowser.open") as browser:
                Application.inspect_ncwu_contracts(self.app)
            worker.assert_not_called()
            browser.assert_not_called()
            self.assertEqual(info.call_count, 1)
            report = str(info.call_args)
            self.assertIn("xh", report)
            self.assertNotIn("SENSITIVE_PERSONAL_ID", report)
            self.assertNotIn("SENSITIVE_SESSION", report)


if __name__ == "__main__":
    import unittest
    unittest.main()
