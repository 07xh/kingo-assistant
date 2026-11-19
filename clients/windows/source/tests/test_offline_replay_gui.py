"""Offline replay must resolve every name it uses (regression for a NameError)."""
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from core.course_models import CourseOffering
from windows_gui import Application


class ReplayLocalNameResolutionTests(TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.sample = Path(self.tempdir.name) / "sample.html"
        self.sample.write_text("<html><table></table></html>", encoding="utf-8")

        path_var = Mock()
        path_var.get.return_value = str(Path(self.tempdir.name) / "config.yaml")
        self.app = SimpleNamespace(
            worker=None,
            path=path_var,
            table=Mock(),
            status=Mock(),
            log=Mock(),
            populate=Mock(),
            _demo_report=None,
            _demo_index=0,
        )
        self.config = {
            "protocol": {},
            "courses": [
                {"key": "A", "course_id": "C1", "class_id": "B1"},
                {"key": "B", "course_id": "C2", "class_id": "B2"},
            ],
        }

    def tearDown(self):
        self.tempdir.cleanup()

    def test_matches_offering_and_reports_local_only(self):
        offering = CourseOffering(course_id="C1", class_id="B1", remain=3)
        with patch("windows_gui.filedialog.askopenfilename", return_value=str(self.sample)), \
             patch("windows_gui.read_config", return_value=self.config), \
             patch("windows_gui._read_local_html", return_value="<html></html>"), \
             patch("windows_gui.inspect_html", return_value={"tables": []}), \
             patch("windows_gui.parse_courses", return_value=[offering]), \
             patch("httpx.AsyncClient", side_effect=AssertionError("replay used network")):
            Application.replay_local(self.app)

        self.app.table.set.assert_any_call("A", "status", "样本余量 3")
        self.app.table.set.assert_any_call("B", "status", "样本中未匹配")
        self.app.populate.assert_called_once_with(self.config)
        self.app.status.set.assert_called_once()
        self.assertIn("离线演练完毕", self.app.status.set.call_args.args[0])
        self.assertIn("没有连接学校", self.app.log.call_args.args[0])

    def test_cancelled_dialog_does_nothing(self):
        with patch("windows_gui.filedialog.askopenfilename", return_value=""):
            Application.replay_local(self.app)
        self.app.table.set.assert_not_called()
        self.app.status.set.assert_not_called()

    def test_replay_is_refused_while_a_worker_runs(self):
        self.app.worker = object()
        with patch("windows_gui.filedialog.askopenfilename") as chooser:
            Application.replay_local(self.app)
        chooser.assert_not_called()


if __name__ == "__main__":
    import unittest

    unittest.main()
