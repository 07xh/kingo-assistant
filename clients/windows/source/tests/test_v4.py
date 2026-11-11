"""V4 offline-only: no real school or push endpoints are contacted."""
import asyncio
import contextlib
import io
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from core.client import CourseItem, KingoClient, RateLimitError
from core.inspector import inspect_html
from core.monitor_state import MonitorStateStore
from core.scheduler import Scheduler
from main import inspect_local_html, replay_sequence
from tests.test_v3 import catalog, cfg


class HtmlInspectorTest(unittest.TestCase):
    def test_gbk_headers_and_strict_privacy(self):
        student = 'SENSITIVE_STUDENT_NAME_001'
        token = 'SENSITIVE_COOKIE_99'
        sample = ("<html><table id='x'><tr><th>课程代码</th><th>课程名称</th>"
                  "<th>任课教师</th><th>剩余名额</th><th>教学班号</th>"
                  "<th>手机号</th></tr><tr><td>C101</td><td>测试课程</td>"
                  f"<td>{student}</td><td>1</td><td>A</td><td>{token}</td>"
                  "</tr></table></html>").encode('gb18030')
        hints = inspect_html(sample)
        self.assertEqual(len(hints), 1)
        self.assertEqual(hints[0].field_indexes, {
            'course_id': 0, 'course_name': 1,
            'teacher': 2, 'remain': 3, 'class_id': 4,
        })
        self.assertTrue(hints[0].required_fields_found)
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / 'private.html'
            file.write_bytes(sample)
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                inspect_local_html(str(file))
            self.assertNotIn(student, output.getvalue())
            self.assertNotIn(token, output.getvalue())
            self.assertNotIn('手机号', output.getvalue())
            self.assertIn('course_id:0', output.getvalue())

    def test_legacy_table_uses_td_for_all_header_cells(self):
        raw = ("<table><tr><td>课程代码</td><td>课程名称</td>"
               "<td>教师</td><td>余量</td><td>教学班号</td></tr>"
               "<tr><td>X1</td><td>测试</td><td>甲</td><td>2</td>"
               "<td>B</td></tr></table>").encode('gb18030')
        hint = inspect_html(raw)[0]
        self.assertTrue(hint.required_fields_found)
        self.assertEqual(hint.field_indexes['class_id'], 4)

    def test_non_header_data_does_not_guess_schema(self):
        raw = b'<table><tr><td>course_id</td><td>course_name</td></tr></table>'
        self.assertEqual(inspect_html(raw), [])

    def test_reject_empty_or_oversized_capture(self):
        for raw in (b'', b'X' * 4_000_001):
            with self.assertRaises(ValueError):
                inspect_html(raw)

    def test_no_external_link_or_script_exposure(self):
        raw = ("<html><script>var token='TOP_SECRET'</script>"
               "<table><tr><th>课程代码</th><th>剩余名额</th></tr>"
               "<tr><td>hello</td><td>1</td></tr></table></html>").encode()
        result = repr(inspect_html(raw))
        self.assertNotIn('TOP_SECRET', result)
        self.assertNotIn('hello', result)


class ReplaySequenceTest(unittest.IsolatedAsyncioTestCase):
    async def test_three_state_transitions_never_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = []
            for i, number in enumerate(('0', '2', '0')):
                f = Path(tmp) / f'{i}.html'
                f.write_bytes(catalog(seats_b=number))
                files.append(str(f))
            config = cfg()
            config['notify'] = {'enabled': True, 'pushplus_token': 'FAKE_TOKEN'}
            config['strategy']['state_file'] = str(Path(tmp) / 'state.json')
            output = io.StringIO()
            with patch('httpx.AsyncClient', side_effect=AssertionError('unexpected network')):
                with contextlib.redirect_stdout(output):
                    await replay_sequence(config, files)
            text = output.getvalue()
            self.assertIn('发现余量', text)
            self.assertIn('已满', text)
            self.assertFalse((Path(tmp) / 'state.json').exists())

    async def test_limits_and_corrupt_frames_fail_without_network(self):
        with self.assertRaises(ValueError):
            await replay_sequence(cfg(), [])
        with self.assertRaises(ValueError):
            await replay_sequence(cfg(), ['file.html'] * 21)
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / 'unexpected.html'
            f.write_bytes(b'<html>login failed</html>')
            with self.assertRaises(Exception):
                await replay_sequence(cfg(), [str(f)])


class ReliableNotifications(unittest.IsolatedAsyncioTestCase):
    class Client:
        def __init__(self):
            self.stop = asyncio.Event()
            self.items = [CourseItem('TEST101', '测试', '教师', 2, 'B')]
        async def fetch_course_list(self, *args):
            return self.items

    async def test_failure_does_not_consume_alert_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = cfg()
            config['notify'] = {'enabled': True}
            file = Path(tmp) / 'state.json'
            config['strategy']['state_file'] = str(file)
            client = self.Client()
            first = Scheduler(client, None, config)
            calls = []
            async def failing(*args):
                calls.append('fail')
                return False
            first.notify = failing
            await first.cycle()
            await first.cycle()
            self.assertEqual(calls, ['fail', 'fail'])
            self.assertFalse(file.exists())
            second = Scheduler(client, None, config)
            async def success(*args):
                calls.append('success')
                return True
            second.notify = success
            await second.cycle()
            self.assertEqual(calls[-1], 'success')
            self.assertEqual(len(MonitorStateStore(file).load()), 1)

    async def test_transport_error_isolated_per_target(self):
        c = cfg()
        c['courses'].append({'key': 'other', 'course_id': 'TEST202'})
        c['notify'] = {'enabled': True}
        client = self.Client()
        client.items.append(CourseItem('TEST202', '第二门', '教师', 1, 'D'))
        watcher = Scheduler(client, None, c)
        attempts = []
        async def unreliable(target, item):
            attempts.append(target['key'])
            if target['key'] == 'target':
                raise RuntimeError('provider offline')
            return True
        watcher.notify = unreliable
        await watcher.cycle()
        self.assertEqual(attempts, ['target', 'other'])
        self.assertEqual(list(watcher._already_notified), ['other'])

    async def test_opt_in_without_token_is_not_a_delivery(self):
        c = cfg()
        c['notify'] = {'enabled': True, 'pushplus_token': '', 'serverchan_token': ''}
        watcher = Scheduler(self.Client(), None, c)
        with patch.dict('os.environ', {'PUSHPLUS_TOKEN': '', 'SERVERCHAN_TOKEN': ''}):
            result = await watcher.notify(c['courses'][0], CourseItem('TEST101', '测试', '', 1))
        self.assertFalse(result)

    async def test_push_200_with_error_code_does_not_count(self):
        c = cfg()
        c['notify'] = {'enabled': True, 'pushplus_token': 'PLACEHOLDER'}
        watcher = Scheduler(self.Client(), None, c)
        transport = httpx.MockTransport(lambda req: httpx.Response(
            200, json={'code': 500, 'msg': 'not accepted'}))
        real = httpx.AsyncClient
        def fake_client(*args, **kwargs):
            return real(transport=transport, follow_redirects=False)
        with patch('core.scheduler.httpx.AsyncClient', side_effect=fake_client):
            result = await watcher.notify(c['courses'][0], CourseItem('TEST101', '测试', '', 1))
        self.assertFalse(result)


class ServerResponseHandling(unittest.IsolatedAsyncioTestCase):
    async def test_403_is_never_retried(self):
        from tests.test_local import config
        calls = []
        def handler(request):
            calls.append(request)
            return httpx.Response(403)
        client = KingoClient(config(), offline_test_mode=True)
        await client.http.aclose()
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            with self.assertRaises(RateLimitError):
                await client.identity()
        finally:
            await client.aclose()
        self.assertEqual(len(calls), 1)


if __name__ == '__main__':
    unittest.main()
