"""V4.1 encoding, notification acknowledgment and local-input regression.

All transport traffic uses httpx.MockTransport; no university requests or
third-party push requests are sent by the tests.
"""
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from core.client import (CourseItem, ProtocolError, decode_html, html_encoding,
                         parse_courses)
from core.scheduler import Scheduler
from core.monitor_state import MonitorStateStore
from main import _read_local_html, replay_html
from tests.test_v3 import cfg


class CharsetTest(unittest.TestCase):
    def setUp(self):
        self.columns = {"columns": {"course_id": 0, "course_name": 1,
                                    "teacher": 2, "remain": 3, "class_id": 4}}
        self.sample = """<html><head><meta charset="utf-8"></head><body>
            <table><tr><td>TEST101</td><td>电路原理</td><td>张老师</td><td>2</td><td>B</td></tr></table>
            </body></html>"""

    def test_utf8_meta_preserves_chinese_and_class_identity(self):
        html = self.sample.encode('utf-8')
        self.assertEqual(html_encoding(html), 'utf-8')
        rows = parse_courses(html, self.columns, ['TEST101'])
        self.assertEqual([(x.course_name,x.teacher,x.class_id,x.remain) for x in rows],
                         [('电路原理','张老师','B',2)])

    def test_gb2312_header_normalizes_to_gb18030(self):
        html = "<table><tr><td>TEST101</td><td>电路</td><td>张老师</td><td>3</td><td>B</td></tr></table>".encode('gbk')
        rows = parse_courses(html, self.columns, content_type='text/html; charset=gb2312')
        self.assertEqual(rows[0].course_name, '电路')
        response = httpx.Response(200, headers={'content-type': 'text/html; charset=gb2312'}, content=html)
        self.assertIn('张老师',decode_html(response))

    def test_charset_conflict_fails_closed(self):
        html = self.sample.encode('utf-8')
        with self.assertRaises(ProtocolError):
            parse_courses(html, self.columns, content_type='text/html; charset=gb2312')

    def test_invalid_utf8_is_not_silently_ignored(self):
        raw = b'<html><meta charset="utf-8"><table><tr><td>TEST101</td><td>\xff</td><td>Teacher</td><td>1</td><td>B</td></tr></table>'
        with self.assertRaises(ProtocolError):
            parse_courses(raw, self.columns)

    def test_unknown_encoding_is_rejected(self):
        with self.assertRaises(ProtocolError):
            html_encoding(b'<meta charset="ISO-8859-1">')


class NoticeAcknowledgmentTest(unittest.IsolatedAsyncioTestCase):
    class Client:
        stop = None
        def __init__(self):
            self.stop=asyncio.Event()
        async def fetch_course_list(self,*args):
            return [CourseItem('TEST101','测试课程','老师',2,'B')]

    async def test_none_acknowledgment_does_not_consume_alert(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=cfg(); c['notify']={'enabled':True}
            p=Path(tmp)/'state.json'; c['strategy']['state_file']=str(p)
            watcher=Scheduler(self.Client(),None,c)
            calls=[]
            async def no_confirmation(*args):
                calls.append(1)
                return None
            watcher.notify=no_confirmation
            await watcher.cycle(); await watcher.cycle()
            self.assertEqual(len(calls),2)
            self.assertFalse(p.exists())

    async def test_http200_html_is_not_delivery(self):
        c=cfg();c['notify']={'enabled':True,'pushplus_token':'EXAMPLE_TOKEN'}
        watcher=Scheduler(self.Client(),None,c)
        transport=httpx.MockTransport(lambda req: httpx.Response(
            200, headers={'content-type':'text/html'}, text='<html>sign in</html>'))
        real=httpx.AsyncClient
        with patch('core.scheduler.httpx.AsyncClient',side_effect=lambda *args,**kw: real(transport=transport)):
            result=await watcher.notify(c['courses'][0],CourseItem('TEST101','测试','',2))
        self.assertFalse(result)

    async def test_json_success_codes_are_accepted(self):
        c=cfg();c['notify']={'enabled':True,'pushplus_token':'EXAMPLE_TOKEN'}
        watcher=Scheduler(self.Client(),None,c)
        transport=httpx.MockTransport(lambda req: httpx.Response(
            200, json={'code':200,'msg':'success'}))
        real=httpx.AsyncClient
        with patch('core.scheduler.httpx.AsyncClient',side_effect=lambda *args,**kw: real(transport=transport)):
            result=await watcher.notify(c['courses'][0],CourseItem('TEST101','测试','',2))
        self.assertTrue(result)

    async def test_http200_json_without_code_is_not_ack(self):
        c=cfg();c['notify']={'enabled':True,'pushplus_token':'EXAMPLE_TOKEN'}
        watcher=Scheduler(self.Client(),None,c)
        transport=httpx.MockTransport(lambda req: httpx.Response(
            200, json={'status':'ok'}))
        real=httpx.AsyncClient
        with patch('core.scheduler.httpx.AsyncClient',side_effect=lambda *args,**kw: real(transport=transport)):
            result=await watcher.notify(c['courses'][0],CourseItem('TEST101','测试','',2))
        self.assertFalse(result)


class ReplaySafetyTest(unittest.TestCase):
    def test_single_file_replay_refuses_symlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            a=Path(tmp)/'real.html'; a.write_text('<html></html>',encoding='utf-8')
            b=Path(tmp)/'shortcut.html'
            try:
                b.symlink_to(a)
            except (OSError,NotImplementedError):
                self.skipTest('symlinks are not supported by this OS')
            with self.assertRaises(ValueError):
                _read_local_html(str(b))
            with self.assertRaises(ValueError):
                replay_html(cfg(),str(b))


if __name__=='__main__':
    unittest.main()
