"""V3 offline regression: class identity, structure fail-closed, replay & state."""
import asyncio
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

from core.client import CourseItem, ProtocolError, parse_courses
from core.monitor_state import MonitorStateStore, StateFileError
from core.scheduler import Scheduler
from main import replay_html


def cfg():
    return {
        "base_url": "https://campus.example.edu", "xnxq": "2026-2027-1",
        "strategy": {"mode": "monitor", "interval_range": [60, 90],
                     "max_cycles": 2, "dry_run": True},
        "courses": [
            {"key": "target", "course_id": "TEST101", "class_id": "B"},
        ],
        "notify": {},
        "protocol": {"columns": {"course_id": 0, "course_name": 1,
                                 "teacher": 2, "remain": 3, "class_id": 4},
                     "table_marker": 'id="actual-courses"'},
    }


def catalog(seats_a="0", seats_b="2"):
    html = ('<html><table id="actual-courses"><tr><th>course</th></tr>'
            f'<tr><td>TEST101</td><td>电路原理</td><td>甲老师</td><td>{seats_a}</td><td>A</td></tr>'
            f'<tr><td>TEST101</td><td>电路原理</td><td>乙老师</td><td>{seats_b}</td><td>B</td></tr>'
            '<tr><td>TEST202</td><td>通信</td><td>TEST101 教师</td><td>3</td><td>C</td></tr>'
            '</table></html>')
    return html.encode('gb18030')


class ClassIdentityTest(unittest.TestCase):
    def test_class_id_disambiguates_same_course(self):
        courses = parse_courses(catalog(), cfg()["protocol"], ["TEST101"])
        self.assertEqual([(c.course_id, c.class_id, c.remain) for c in courses],
                         [("TEST101", "A", 0), ("TEST101", "B", 2)])
        self.assertEqual([c.class_id for c in courses if Scheduler.matches(cfg()["courses"][0], c)],
                         ["B"])

    def test_teacher_contains_target_id_not_falsely_matched(self):
        courses = parse_courses(catalog(), cfg()["protocol"], ["TEST202"])
        self.assertEqual([(c.course_id, c.class_id) for c in courses], [("TEST202", "C")])

    def test_schema_mismatch_is_error_even_with_target_filter(self):
        wrong = b'<html><h2>Please sign in</h2><table><tr><td>misc</td></tr></table></html>'
        with self.assertRaises(ProtocolError):
            parse_courses(wrong, cfg()["protocol"], ["TEST101"])
        p = cfg()["protocol"].copy()
        p.pop("table_marker")
        with self.assertRaises(ProtocolError):
            parse_courses(b'<html><h2>Auth failed</h2></html>', p, ["TEST101"])

    def test_empty_with_explicit_marker(self):
        p = cfg()["protocol"].copy()
        p["empty_marker"] = "没有符合条件的课程"
        p.pop("table_marker")
        self.assertEqual(parse_courses('<html><p>没有符合条件的课程</p></html>'.encode('gbk'), p), [])
        with self.assertRaises(ProtocolError):
            parse_courses(b'<html></html>', p)

    def test_unrelated_valid_course_is_not_missing_structure(self):
        p = cfg()["protocol"]
        other = (b'<table id="actual-courses"><tr><td>OTHER</td><td>Name</td>'
                 b'<td>Teacher</td><td>0</td><td>Z</td></tr></table>')
        self.assertEqual(parse_courses(other, p, ["TARGET_NOT_LISTED"]), [])

    def test_replay_html_remains_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / 'fake.html'
            file.write_bytes(catalog())
            output = io.StringIO()
            # Rich Console defaults to stdout; redirect output in local test.
            with redirect_stdout(output):
                replay_html(cfg(), str(file))
            self.assertIn("本地样本余量 2", output.getvalue())
            self.assertIn("没有访问教务系统", output.getvalue())


class ConfigSafetyTest(unittest.TestCase):
    def test_real_query_requires_verified_table_marker(self):
        from main import validate
        config = cfg()
        config["auth"] = {"cookie": "fake-for-offline-test"}
        config["protocol"].update({
            "verified": True,
            "identity_path": "/mine", "identity_referer": "/",
            "identity_selector": "#person", "entry": "/list",
            "entry_referer": "/", "list": "/catalog", "term_field": "term",
            "list_method": "POST"
        })
        config["protocol"].pop("table_marker")
        with self.assertRaisesRegex(ValueError, "table_marker"):
            validate(config)


class NotificationConsentTest(unittest.IsolatedAsyncioTestCase):
    async def test_no_webhook_without_explicit_consent(self):
        dummy = SimpleNamespace(stop=asyncio.Event())
        config = cfg()
        config["notify"] = {"pushplus_token": "FAKE", "enabled": False}
        watcher = Scheduler(dummy, None, config)
        await watcher.notify(config["courses"][0], CourseItem("TEST101", "测试", "教师", 1, "B"))
        # HTTP requests would error without a real mock transport; the function
        # must return before creating an HTTP client.


class NotificationStateTest(unittest.TestCase):
    def test_save_and_reload_only_hashed_identifiers(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / 'state.json'
            store = MonitorStateStore(file)
            digest = store.fingerprint('TEST101', 'B')
            store.save({"target": digest})
            self.assertEqual(store.load(), {"target": digest})
            raw = file.read_text()
            self.assertNotIn("TEST101", raw)
            self.assertNotIn("B", raw)
            self.assertEqual(len(digest), 64)

    def test_corruption_cannot_be_silently_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / 'state.json'
            file.write_text('{oops', encoding='utf8')
            store = MonitorStateStore(file)
            with self.assertRaises(StateFileError):
                store.load()
            self.assertEqual(file.read_text(), '{oops')
            file.write_text(json.dumps({"schema": "other", "active": {}}))
            with self.assertRaises(StateFileError):
                store.load()


class NotificationTransitionTest(unittest.IsolatedAsyncioTestCase):
    async def test_disabling_notifications_does_not_consume_future_alert(self):
        class Dummy:
            stop = asyncio.Event()
            async def fetch_course_list(self, *args):
                return [CourseItem("TEST101", "电路原理", "乙老师", 1, "B")]

        with tempfile.TemporaryDirectory() as tmp:
            cfg_local = cfg()
            path = Path(tmp) / 'notify.json'
            cfg_local["strategy"]["state_file"] = str(path)
            cfg_local["notify"]["enabled"] = False
            offline = Scheduler(Dummy(), None, cfg_local)
            offline.notify = lambda *_: asyncio.sleep(0)
            await offline.cycle()
            self.assertFalse(path.exists())
            cfg_local["notify"]["enabled"] = True
            actual = Scheduler(Dummy(), None, cfg_local)
            events = []
            async def capture(*_):
                events.append('notify')
                return True  # Provider explicitly accepted the message.
            actual.notify = capture
            await actual.cycle()
            self.assertEqual(events, ['notify'])
            self.assertTrue(path.exists())

    async def test_one_alert_across_restarts_rearm_after_full(self):
        class Dummy:
            def __init__(self):
                self.stop = asyncio.Event()
                self.items = [CourseItem("TEST101", "电路原理", "乙老师", 2, "B")]
                self.calls = 0
            async def fetch_course_list(self, *args):
                self.calls += 1
                return self.items

        with tempfile.TemporaryDirectory() as tmp:
            c = cfg()
            c["strategy"]["state_file"] = str(Path(tmp) / "state.json")
            c["notify"]["enabled"] = True
            captured = []
            async def fake_notify(target, item):
                captured.append(item.remain)
                return True  # Successful provider acknowledgment.

            dummy = Dummy()
            one = Scheduler(dummy, None, c)
            one.notify = fake_notify
            await one.cycle()
            await one.cycle()
            self.assertEqual(captured, [2])
            # Restart process; same available class must not re-alert.
            two = Scheduler(dummy, None, c)
            two.notify = fake_notify
            await two.cycle()
            self.assertEqual(captured, [2])
            dummy.items = [CourseItem("TEST101", "电路原理", "乙老师", 0, "B")]
            await two.cycle()
            three = Scheduler(dummy, None, c)
            three.notify = fake_notify
            dummy.items = [CourseItem("TEST101", "电路原理", "乙老师", 1, "B")]
            await three.cycle()
            self.assertEqual(captured, [2, 1])
            self.assertEqual(dummy.calls, 5)


if __name__ == '__main__':
    unittest.main()
