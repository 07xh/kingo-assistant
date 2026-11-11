"""Safety and structural tests for offline-only NCWU/Xiqueer HAR audits."""
import json
from pathlib import Path
import tempfile
import unittest
from core.har_audit import summarize_har, format_summary


def capture_entry(url, content, status=200, method='POST'):
    return {'request': {'url': url, 'method': method, 'headers':
                        [{'name': 'Cookie', 'value': 'SECRET_SESSION'}],
                        'postData': {'text': 'PRIVATE_PASSWORD'}},
            'response': {'status': status, 'content':
                         {'mimeType': 'application/json', 'text': content}}}


class HarStructureTest(unittest.TestCase):
    def run_capture(self, entries):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'local.har'
            p.write_text(json.dumps({'log': {'entries': entries}}), encoding='utf-8')
            return summarize_har(p)

    def test_allowlisted_timetable_shape_and_gateway_gate(self):
        timetable = {'xn': 'PRIVATE', 'xq': 'PRIVATE', 'zc': '1', 'sjhjinfo': []}
        timetable.update({f'week{day}': [] for day in range(1, 8)})
        entries = [
            capture_entry('https://jwmis.ncwu.edu.cn/hsjw//wap/mycourseschedule.action', json.dumps(timetable)),
            capture_entry('https://api.xiqueer.com/manager//wap/wapController.jsp',
                          json.dumps({'message': '当前时间不是有效的选课时间区段!', 'student': 'PRIVATE_NAME'}, ensure_ascii=False)),
            capture_entry('https://api.xiqueer.com/manager/wap/wapController.jsp', '{}'),
            capture_entry('https://api.xiqueer.com.evil.test/manager/wap/wapController.jsp',
                          '当前时间不是有效的选课时间区段'),
        ]
        report = self.run_capture(entries)
        self.assertEqual(report.matching_school_origin, 1)
        self.assertEqual(report.ncwu_timetable_schema_matches, 1)
        self.assertEqual(report.xiqueer_gateway_requests, 2)
        self.assertEqual(report.selection_window_closed_observations, 1)
        shown = format_summary(report)
        self.assertIn('真实可选课程目录：未核验', shown)
        for secret in ('PRIVATE', 'SECRET_SESSION', 'PRIVATE_NAME', 'mycourseschedule.action'):
            self.assertNotIn(secret, shown)

    def test_timetable_shape_rejects_fake_available_seats(self):
        rows = [capture_entry('https://jwmis.ncwu.edu.cn/hsjw/wap/mycourseschedule.action',
                              json.dumps({'week1': [], 'remaining': 10})),
                capture_entry('https://jwmis.ncwu.edu.cn/hsjw/wap/another.action',
                              json.dumps({'xn': 'X', 'xq': 'Y', 'zc': '1',
                                          **{f'week{i}': [] for i in range(1, 8)},
                                          'sjhjinfo': []}))]
        self.assertEqual(self.run_capture(rows).ncwu_timetable_schema_matches, 0)

    def test_reject_symlink_and_oversize(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'capture.har'
            p.write_text('{"log":{"entries":[]}}', encoding='utf-8')
            link = Path(d) / 'shortcut.har'
            try:
                link.symlink_to(p)
            except OSError:
                pass
            else:
                with self.assertRaises(ValueError):
                    summarize_har(link)
            p.write_bytes(b'0' * (25 * 1024 * 1024 + 1))
            with self.assertRaises(ValueError):
                summarize_har(p)

    def test_never_uses_closed_message_as_open_window(self):
        captured = self.run_capture([capture_entry(
            'https://api.xiqueer.com/manager/wap/wapController.jsp',
            '当前时间不是有效的选课时间区段')])
        self.assertEqual(captured.selection_window_closed_observations, 1)
        self.assertEqual(captured.ncwu_timetable_schema_matches, 0)
        self.assertIn('未核验', format_summary(captured))

if __name__ == '__main__':
    unittest.main()
