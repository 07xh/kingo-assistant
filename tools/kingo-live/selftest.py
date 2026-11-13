#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
End-to-end verification over a REAL HTTP loopback server.

These are actual TCP connections, actual HTTP requests, actual JSON bodies,
actual Server-Timing headers and actual enrollment readback. The loopback
server stands in for the target host because no live session is configured;
live_client.py itself contains no simulation logic.

    python selftest.py -v
"""

from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import live_client as lc

HERE = Path(__file__).parent


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.catalog_rows = [
            {"kcid": "C001", "skbj": "T01", "kcmc": "课程A", "jsxm": "王老师", "kyl": "2"},
            {"kcid": "C002", "skbj": "T02", "kcmc": "课程B", "jsxm": "李老师", "kyl": "0"},
        ]
        self.enrolled: list[dict] = []
        self.submit_count = 0
        self.last_get_headers: dict = {}
        self.last_post_headers: dict = {}
        self.force_429 = False
        self.empty_readback = False

    def reset(self):
        with self.lock:
            self.catalog_rows = [
                {"kcid": "C001", "skbj": "T01", "kcmc": "课程A", "jsxm": "王老师", "kyl": "2"},
                {"kcid": "C002", "skbj": "T02", "kcmc": "课程B", "jsxm": "李老师", "kyl": "0"},
            ]
            self.enrolled = []
            self.submit_count = 0
            self.last_get_headers = {}
            self.last_post_headers = {}
            self.force_429 = False
            self.empty_readback = False


class Handler(BaseHTTPRequestHandler):
    state: State = State()
    protocol_version = "HTTP/1.0"

    def log_message(self, *args):
        pass

    def _send(self, code: int, obj, extra=None, content_type="application/json"):
        body = (json.dumps(obj, ensure_ascii=False).encode("utf-8")
                if content_type == "application/json" else str(obj).encode("utf-8"))
        self.send_response(code)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Server-Timing", "queue;dur=8.5, db;dur=22.1")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        with self.state.lock:
            self.state.last_get_headers = dict(self.headers)
        if self.path.startswith("/catalog.html"):
            html = ("<table><tr><th>课程号</th><th>教学班</th><th>课程名</th>"
                    "<th>教师</th><th>余量</th></tr>"
                    "<tr><td>C001</td><td>T01</td><td>课程A</td><td>王老师</td><td>2</td></tr>"
                    "<tr><td>C002</td><td>T02</td><td>课程B</td><td>李老师</td><td>0</td></tr>"
                    "</table>")
            self._send(200, html, content_type="text/html")
            return
        if self.path.startswith("/catalog"):
            time.sleep(0.035)  # real server-side work so TTFB is measurable
            with self.state.lock:
                rows = list(self.state.catalog_rows)
            self._send(200, {"data": {"rows": rows}})
            return
        if self.path.startswith("/enrollment"):
            with self.state.lock:
                rows = [] if self.state.empty_readback else list(self.state.enrolled)
            self._send(200, {"data": {"rows": rows}})
            return
        self._send(404, {"error": "not_found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b""
        with self.state.lock:
            self.state.last_post_headers = dict(self.headers)
            self.state.submit_count += 1
        if self.state.force_429:
            self._send(429, {"error": "rate_limited"}, {"Retry-After": "1"})
            return
        fields = urllib.parse.parse_qs(raw.decode("utf-8", errors="replace"))
        kcid = fields.get("kcid", [""])[0]
        skbj = fields.get("skbj", [""])[0]
        with self.state.lock:
            if not self.state.empty_readback:
                self.state.enrolled.append({"kcid": kcid, "skbj": skbj})
        self._send(200, {"ok": True})


class LiveHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.state = State()
        Handler.state = cls.state
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dir = Path(cls.tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def setUp(self):
        self.state.reset()

    def tag(self) -> str:
        return self.id().split(".")[-1]

    def cfg(self, **strategy):
        c = json.loads((HERE / "config.example.json").read_text(encoding="utf-8"))
        c["base_url"] = f"http://127.0.0.1:{self.port}"
        c["catalog"]["path"] = "/catalog"
        c["submit"]["path"] = "/submit"
        c["enrollment"]["path"] = "/enrollment"
        c["cookie"] = ""
        c["verify_tls"] = False
        c["budget"]["database"] = str(self.dir / f"budget_{self.tag()}.sqlite")
        c["journal"]["path"] = str(self.dir / f"journal_{self.tag()}.jsonl")
        c["targets"] = [{"course_id": "C001", "class_id": "T01"}]
        c["strategy"].update(strategy)
        return c

    def rec(self, suffix=""):
        return str(self.dir / f"records_{self.tag()}{suffix}.jsonl")

    def test_probe_uses_real_http_and_server_timing(self):
        eng = lc.Engine(self.cfg(), records_path=self.rec())
        a = eng.probe()
        self.assertEqual(a.status, 200)
        self.assertIn("parsed=2", a.reason)
        self.assertTrue(a.server_timing)
        self.assertAlmostEqual(a.queue_ms, 8.5, places=1)
        self.assertAlmostEqual(a.tx_ms, 22.1, places=1)
        self.assertGreaterEqual(a.connect_ms, 0.0)
        self.assertGreaterEqual(a.network_ms, 0.0)
        self.assertGreater(a.discover_ms, 0.0)
        self.assertIn("C001", eng.last_probe_body.decode("utf-8"))

    def test_monitor_detects_a_real_seat(self):
        eng = lc.Engine(self.cfg(poll_interval_ms=1), records_path=self.rec())
        found = eng.monitor(1)
        self.assertEqual(len(found), 1)
        self.assertGreater(found[0].discover_ms, 0.0)
        self.assertIn("seat=2", found[0].reason)
        self.assertEqual(found[0].course_id, "C001")

    def test_enrollment_only_confirms_via_official_readback(self):
        eng = lc.Engine(self.cfg(), records_path=self.rec("_ok"))
        out = eng.enroll(confirm=True)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0].status, 200)
        self.assertTrue(out[0].success)
        self.assertEqual(out[0].reason, "confirmed")
        self.assertGreater(out[0].submit_ms, 0.0)
        self.assertGreater(out[0].confirm_ms, 0.0)
        self.assertGreaterEqual(self.state.submit_count, 1)

        self.state.empty_readback = True
        cfg2 = self.cfg()
        cfg2["journal"]["path"] = str(self.dir / f"journal_{self.tag()}_2.jsonl")
        eng2 = lc.Engine(cfg2, records_path=self.rec("_no"))
        out2 = eng2.enroll(confirm=True)
        self.assertEqual(len(out2), 1)
        self.assertFalse(out2[0].success)
        self.assertEqual(out2[0].reason, "readback_no_match")

    def test_html_catalog_channel_parses_real_table(self):
        cfg = self.cfg()
        cfg["catalog"]["path"] = "/catalog.html"
        cfg["catalog"]["format"] = "html"
        cfg["catalog"]["columns"] = {"course_id": 0, "class_id": 1,
                                     "course_name": 2, "teacher": 3, "remain": 4}
        eng = lc.Engine(cfg, records_path=self.rec())
        a = eng.probe()
        self.assertEqual(a.status, 200)
        self.assertIn("parsed=3", a.reason)  # header row + 2 data rows

    def test_direct_api_sends_real_json_headers(self):
        eng = lc.Engine(self.cfg(direct_api=True), records_path=self.rec())
        eng.probe()
        self.assertEqual(self.state.last_get_headers.get("Accept"),
                         "application/json")
        self.assertEqual(self.state.last_get_headers.get("X-Requested-With"),
                         "XMLHttpRequest")

    def test_429_cooldown_and_ignore_retry_after(self):
        self.state.force_429 = True
        eng = lc.Engine(self.cfg(), records_path=self.rec("_a"))
        out = eng.enroll(confirm=True)
        self.assertEqual(out[0].status, 429)
        self.assertFalse(out[0].success)

        before = self.state.submit_count
        cfg2 = self.cfg(respect_retry_after=False)
        cfg2["budget"]["database"] = str(Path(eng.budget.db_path))
        cfg2["journal"]["path"] = str(self.dir / f"journal_{self.tag()}_2.jsonl")
        eng2 = lc.Engine(cfg2, records_path=self.rec("_b"))
        eng2.enroll(confirm=True)
        self.assertGreater(self.state.submit_count, before)
        self.state.force_429 = False

    def test_measure_over_real_recorded_attempts(self):
        path = self.rec()
        eng = lc.Engine(self.cfg(), records_path=path)
        eng.probe()
        eng.monitor(1)
        eng.enroll(confirm=True)
        m = lc.measure(path)
        self.assertGreaterEqual(m["attempts"], 3)
        self.assertGreaterEqual(m["confirmed"], 1)
        self.assertGreater(m["confirm_p50"], 0.0)
        self.assertGreater(m["queue_p50"], 0.0)
        self.assertGreater(m["tx_p50"], 0.0)
        self.assertGreaterEqual(m["with_server_timing"], 1)
        print("\n[measure]", json.dumps(m, ensure_ascii=False))

    def test_enroll_requires_explicit_opt_in(self):
        eng = lc.Engine(self.cfg(), records_path=self.rec())
        with self.assertRaises(RuntimeError):
            eng.enroll(confirm=False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
