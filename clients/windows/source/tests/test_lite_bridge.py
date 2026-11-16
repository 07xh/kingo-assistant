"""Contract tests: Lite IPC never imports account secrets into the view."""
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import lite_bridge
from core.desktop_settings import load_config, save_config_atomic


class LiteBridgeTests(TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.patcher = patch.object(lite_bridge, "app_dir", return_value=Path(self.tmp.name))
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_snapshot_uses_existing_local_config_and_no_secret_fields(self):
        path = lite_bridge.ensure_config()
        config = load_config(path)
        config["auth"]["cookie"] = "very-secret-cookie"
        # Saving normalizes credentials, so put test fixture directly on disk.
        import yaml
        path.write_text(yaml.safe_dump(config), encoding="utf-8")
        view = lite_bridge.get_snapshot()
        self.assertEqual(view["data_source"], "local_config")
        self.assertEqual(view["catalog_status"], "not_checked")
        self.assertFalse(view["automatic_submit_available"])
        self.assertNotIn("very-secret-cookie", str(view))
        self.assertNotIn("auth", view)

    def test_save_updates_course_fields_without_changing_protocol(self):
        before = load_config(lite_bridge.ensure_config())
        data = lite_bridge.save_courses([{
            "key": "A", "course_name": "高等数学", "course_id": "MATH-01",
            "class_id": "01", "teacher": "老师甲", "group": "", "time_slots": "",
            "auth": "attempted-injection"
        }])
        after = load_config(lite_bridge.ensure_config())
        self.assertEqual(data["courses"][0]["course_name"], "高等数学")
        self.assertEqual(after["protocol"], before["protocol"])
        self.assertNotIn("auth", after["courses"][0])
        self.assertFalse(data["automatic_submit_available"])

    def test_invalid_course_rejected_without_writing(self):
        path = lite_bridge.ensure_config()
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            lite_bridge.save_courses([{"key": "", "course_name": "无效"}])
        self.assertEqual(path.read_bytes(), before)

    def test_more_than_thirty_courses_is_rejected(self):
        with self.assertRaises(ValueError):
            lite_bridge.save_courses([{"key": str(x), "course_name": "test"} for x in range(31)])
