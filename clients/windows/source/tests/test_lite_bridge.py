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

    def test_existing_ancillary_fields_survive_visible_edits(self):
        path = lite_bridge.ensure_config()
        original = load_config(path)
        original["courses"][0]["extra_local_hint"] = "retain"
        save_config_atomic(path, original)
        lite_bridge.save_courses([{
            "key": "A", "course_name": "高等数学", "course_id": "MATH-01",
            "class_id": "01", "teacher": "老师甲", "group": "", "time_slots": ""
        }])
        updated = load_config(path)
        self.assertEqual(updated["courses"][0]["extra_local_hint"], "retain")


    def test_local_preflight_never_asserts_school_session(self):
        lite_bridge.ensure_config()
        report = lite_bridge.get_preflight()
        self.assertEqual(report["source"], "local_only")
        self.assertFalse(report["school_verified"])
        self.assertTrue(any(
            check["label"] == "本人登录会话" and not check["passed"]
            for check in report["checks"]
        ))

    def test_plan_switch_preserves_protocol_and_creates_recovery_backup(self):
        path = lite_bridge.ensure_config()
        original = load_config(path)
        lite_bridge.store_plan("备选一")
        changed = lite_bridge.save_courses([{
            "key": "NEW", "course_name": "物理实验", "course_id": "PHY01",
            "class_id": "P01", "teacher": "", "group": "", "time_slots": ""
        }])
        self.assertEqual(changed["courses"][0]["key"], "NEW")
        restored = lite_bridge.use_plan("备选一")
        self.assertEqual(restored["courses"][0]["key"], original["courses"][0]["key"])
        self.assertEqual(load_config(path)["protocol"], original["protocol"])
        self.assertIn(restored["backup_name"], lite_bridge.get_plans()["plans"])

    def test_plan_list_and_delete(self):
        lite_bridge.ensure_config()
        lite_bridge.store_plan("教学计划")
        self.assertIn("教学计划", lite_bridge.get_plans()["plans"])
        lite_bridge.remove_plan("教学计划")
        self.assertNotIn("教学计划", lite_bridge.get_plans()["plans"])

    def test_strategy_save_preserves_school_policy(self):
        path = lite_bridge.ensure_config()
        original = load_config(path)
        data = {
            "semester": original["xnxq"],
            "mode": "monitor",
            "target_time": original["strategy"]["target_time"],
            "interval_low": 120, "interval_high": 120,
            "max_cycles": 12, "max_duration_minutes": 60,
        }
        new = lite_bridge.save_strategy(data)
        actual = load_config(path)
        self.assertEqual(new["interval_seconds"], [120, 120])
        self.assertEqual(actual["server_policy"], original["server_policy"])
        self.assertEqual(actual["protocol"], original["protocol"])
        self.assertTrue(actual["strategy"]["dry_run"])

    def test_strategy_rejects_unapproved_fields_without_writing(self):
        path = lite_bridge.ensure_config()
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            lite_bridge.save_strategy({
                "mode": "monitor", "protocol_verified": True,
            })
        self.assertEqual(before, path.read_bytes())

    def test_invalid_course_rejected_without_writing(self):
        path = lite_bridge.ensure_config()
        before = path.read_bytes()
        with self.assertRaises(ValueError):
            lite_bridge.save_courses([{"key": "", "course_name": "无效"}])
        self.assertEqual(path.read_bytes(), before)

    def test_more_than_thirty_courses_is_rejected(self):
        with self.assertRaises(ValueError):
            lite_bridge.save_courses([{"key": str(x), "course_name": "test"} for x in range(31)])
