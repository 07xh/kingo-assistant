#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
End-to-end tests for the optimization layer, over a REAL HTTP/1.1 server.

Every assertion is about an actual TCP connection, an actual response header,
an actual JSON body, an actual SQLite budget/journal file, or an actual
persistent-connection reuse. No simulation is involved.

    python test_optimizations.py -v
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
import optimizations as opt

HERE = Path(__file__).parent


class LabState:
    def __init__(self):
        self.lock = threading.Lock()
        self.reset()

    def reset(self):
        with self.lock:
            self.catalog_rows = [
                {"kcid": "C001", "skbj": "T01", "kcmc": "课程A",
                 "jsxm": "王老师", "kyl": "2"},
                {"kcid": "C002", "skbj": "T02", "kcmc": "课程B",
                 "jsxm": "李老师", "kyl": "0"},
            ]
            self.enrolled: list = []
            self.full_courses: set = set()
            self.session_expired = False
            self.empty_readback = False
            self.readback_status = 200
            self.silent_reject = False
            self.submit_count = 0
            self.last_get_headers: dict = {}


class LabHandler(BaseHTTPRequestHandler):
    state: LabState = LabState()
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _send(self, code, obj, extra=None, content_type="application/json"):
        body = (json.dumps(obj, ensure_ascii=False).encode("utf-8")
                if content_type == "application/json" else str(obj).encode("utf-8"))
        self.send_response(code)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Server-Timing", "queue;dur=8.5, db;dur=22.1")
        self.send_header("X-Server-Time", str(int(time.time() * 1000)))
        self.send_header("Connection", "keep-alive")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        with self.state.lock:
            self.state.last_get_headers = dict(self.headers)
        if self.path.startswith("/catalog"):
            time.sleep(0.02)
            with self.state.lock:
                rows = list(self.state.catalog_rows)
            self._send(200, {"data": {"rows": rows}})
            return
        if self.path.startswith("/session"):
            if self.state.session_expired:
                self._send(200, "<html>请先登录</html>", content_type="text/html")
            else:
                self._send(200, {"ok": True})
            return
        if self.path.startswith("/enrollment"):
            status = self.state.readback_status
            if status != 200:
                self._send(status, {"error": "readback_unavailable"})
                return
            with self.state.lock:
                rows = [] if self.state.empty_readback else list(self.state.enrolled)
            self._send(200, {"data": {"rows": rows}})
            return
        self._send(404, {"error": "not_found"})

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b""
        fields = urllib.parse.parse_qs(raw.decode("utf-8", errors="replace"))
        kcid = fields.get("kcid", [""])[0]
        skbj = fields.get("skbj", [""])[0]
        with self.state.lock:
            self.state.submit_count += 1
            is_full = kcid in self.state.full_courses
        if is_full:
            self._send(200, {"ok": False, "msg": "名额已满"})
            return
        with self.state.lock:
            if not self.state.silent_reject:
                self.state.enrolled.append({"kcid": kcid, "skbj": skbj})
        self._send(200, {"ok": True})


class OptimizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ThreadingHTTPServer.daemon_threads = True
        cls.state = LabState()
        LabHandler.state = cls.state
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), LabHandler)
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

    def tag(self):
        return self.id().split(".")[-1]

    def cfg(self, *, targets=None, keep_alive=False, samples=5,
            clock_header="Date", probes=2, **strategy):
        c = json.loads((HERE / "config.example.json").read_text(encoding="utf-8"))
        c["base_url"] = f"http://127.0.0.1:{self.port}"
        c["cookie"] = ""
        c["verify_tls"] = False
        c["catalog"]["path"] = "/catalog"
        c["submit"]["path"] = "/submit"
        c["enrollment"]["path"] = "/enrollment"
        c["clock"]["path"] = "/catalog"
        c["clock"]["samples"] = samples
        c["clock"]["header"] = clock_header
        c["warmup"]["path"] = "/session"
        c["warmup"]["probes"] = probes
        c["warmup"]["keep_alive"] = keep_alive
        c["budget"]["database"] = str(self.dir / f"budget_{self.tag()}.sqlite")
        c["journal"]["path"] = str(self.dir / f"journal_{self.tag()}.jsonl")
        c["targets"] = targets or [{"course_id": "C001", "class_id": "T01"}]
        c["strategy"].update(strategy)
        c["strategy"]["poll_interval_ms"] = 1.0
        return c

    def rec(self, suffix=""):
        return str(self.dir / f"records_{self.tag()}{suffix}.jsonl")

    # ---- P1 clock ---------------------------------------------------------
    def test_clock_sync_samples_real_date_header(self):
        eng = lc.Engine(self.cfg(samples=5), records_path=self.rec())
        rep = eng.sync_clock()
        self.assertTrue(rep["synced"])
        self.assertEqual(rep["samples"], 5)
        self.assertEqual(rep["precision_ms"], 1000.0)
        self.assertLess(abs(rep["offset_ms"]), 2000.0)
        self.assertEqual(len(rep["sample_rtt_ms"]), 5)
        eng.close()

    def test_clock_sync_custom_ms_header_is_millisecond_precise(self):
        eng = lc.Engine(self.cfg(samples=4, clock_header="X-Server-Time"),
                        records_path=self.rec())
        rep = eng.sync_clock()
        self.assertTrue(rep["synced"])
        self.assertEqual(rep["precision_ms"], 1.0)
        self.assertLess(abs(rep["offset_ms"]), 500.0)
        eng.close()

    def test_wait_until_aligns_on_estimated_server_clock(self):
        eng = lc.Engine(self.cfg(samples=3, clock_header="X-Server-Time"),
                        records_path=self.rec())
        eng.sync_clock()
        target = eng.clock.now_server() + 0.12
        r = eng.clock.wait_until(target, lead_ms=20.0, max_wait_s=3.0)
        self.assertTrue(r["aligned"])
        self.assertGreaterEqual(r["waited_ms"], 60.0)
        self.assertLess(r["waited_ms"], 400.0)
        eng.close()

    # ---- P1 prewarm -------------------------------------------------------
    def test_warmup_probes_real_session_and_reuses_connection(self):
        eng = lc.Engine(self.cfg(keep_alive=True, probes=2), records_path=self.rec())
        rep = eng.warmup()
        self.assertTrue(rep["all_auth_ok"])
        self.assertTrue(rep["connection_reused"])
        self.assertEqual(rep["probes_per_transport"], 2)
        self.assertEqual(rep["results"][0]["status"], 200)
        self.assertGreaterEqual(rep["handshake_p50_ms"], 0.0)
        eng.close()

    def test_warmup_detects_expired_session(self):
        self.state.session_expired = True
        eng = lc.Engine(self.cfg(keep_alive=False, probes=1), records_path=self.rec())
        rep = eng.warmup()
        self.assertFalse(rep["all_auth_ok"])
        self.assertFalse(rep["results"][0]["auth_ok"])
        eng.close()

    # ---- P2 release events -------------------------------------------------
    def test_release_monitor_emits_reopened_only_on_real_change(self):
        eng = lc.Engine(self.cfg(), records_path=self.rec())
        first = eng.watch_events(1)
        self.assertTrue(any(e["kind"] == "new" for e in first["events"]))
        self.assertEqual(first["monitor"]["polls"], 1)

        with self.state.lock:
            self.state.catalog_rows[1]["kyl"] = "3"
        second = eng.watch_events(1)
        kinds = [(e["kind"], e["course_id"], e["previous_remain"], e["remain"])
                 for e in second["events"]]
        self.assertIn(("reopened", "C002", 0, 3), kinds)
        self.assertEqual(second["monitor"]["polls"], 2)
        eng.close()

    def test_exact_match_requires_class_id(self):
        eng = lc.Engine(self.cfg(), records_path=self.rec())
        r = eng.budgeted(eng.transports[0], "GET", "/catalog")
        offers = lc.parse_catalog(eng.cfg["catalog"], r)
        self.assertIsNotNone(opt.FailoverPlanner.match(
            offers, {"course_id": "C001", "class_id": "T01"}))
        self.assertIsNone(opt.FailoverPlanner.match(
            offers, {"course_id": "C001", "class_id": "T99"}))
        self.assertIsNotNone(opt.FailoverPlanner.match(
            offers, {"course_id": "C001", "class_id": ""}))
        eng.close()

    # ---- P2/P6 failover + failure classification --------------------------
    def test_failover_skips_full_target_and_confirms_next(self):
        with self.state.lock:
            self.state.catalog_rows[1]["kyl"] = "1"
            self.state.full_courses = {"C001"}
        targets = [{"course_id": "C001", "class_id": "T01", "priority": 1},
                   {"course_id": "C002", "class_id": "T02", "priority": 2}]
        eng = lc.Engine(self.cfg(targets=targets), records_path=self.rec())
        out = eng.enroll(confirm=True)
        hist = eng.planner.report()["history"]
        self.assertEqual(len(hist), 2)
        self.assertEqual(hist[0]["kind"], "business_full")
        self.assertEqual(hist[0]["action"], "next")
        self.assertEqual(hist[1]["kind"], "confirmed")
        self.assertTrue(any(a.success for a in out))
        self.assertEqual(self.state.submit_count, 2)
        eng.close()

    def test_unknown_readback_keeps_pending_and_reconcile_resolves(self):
        self.state.readback_status = 503
        eng = lc.Engine(self.cfg(), records_path=self.rec())
        out = eng.enroll(confirm=True)
        self.assertEqual(out[0].reason, "readback_unavailable")
        self.assertEqual(len(eng.journal.pending_fingerprints()), 1)

        # A second run must be blocked by the unresolved intent, not retry.
        blocked = eng.enroll(confirm=True)
        self.assertEqual(blocked[0].reason, "pending_intent_needs_reconciliation")
        self.assertEqual(self.state.submit_count, 1)

        self.state.readback_status = 200
        rep = eng.reconcile()
        self.assertEqual(rep["pending_before"], 1)
        self.assertEqual(rep["resolved"], 1)
        self.assertEqual(rep["remaining_pending"], 0)
        # The submission actually landed; only the readback had failed.
        # Reconciliation must recover it as confirmed, not reject it.
        self.assertEqual(rep["confirmed"], 1)
        self.assertEqual(rep["rejected"], 0)
        eng.close()

    def test_reconcile_rejects_intent_absent_from_official_list(self):
        self.state.readback_status = 503
        self.state.silent_reject = True
        eng = lc.Engine(self.cfg(), records_path=self.rec())
        eng.enroll(confirm=True)
        self.assertEqual(len(eng.journal.pending_fingerprints()), 1)

        self.state.readback_status = 200
        rep = eng.reconcile()
        self.assertEqual(rep["confirmed"], 0)
        self.assertEqual(rep["rejected"], 1)
        self.assertEqual(rep["remaining_pending"], 0)
        eng.close()

    def test_classifier_action_table(self):
        cl = opt.FailureClassifier(self.cfg())
        cases = [
            ((401, b"", False, ""), ("auth", "halt")),
            ((429, b"", False, ""), ("rate_limited", "cooldown")),
            ((503, b"", False, ""), ("overload", "backoff")),
            ((200, b'{"ok":false,"msg":"name already full"}', False, ""),
             ("business_full", "next")),
            ((200, b'{"msg":"not in selection time"}', False, ""),
             ("window_closed", "wait")),
            ((200, b'{"ok":false}', False, ""), ("business_rejected", "next")),
            ((200, b"", True, ""), ("confirmed", "stop")),
            ((200, b"", False, ""), ("pending", "halt")),
            ((0, b"", False, "timeout"), ("unknown", "halt")),
        ]
        for args, expect in cases:
            d = cl.classify(*args)
            self.assertEqual((d.kind, d.action), expect,
                             f"args={args} got={(d.kind, d.action)}")

    # ---- full chain --------------------------------------------------------
    def test_auto_read_only_chain_does_not_submit(self):
        eng = lc.Engine(self.cfg(), records_path=self.rec())
        rep = eng.auto(confirm=False, cycles=1)
        self.assertTrue(rep["clock"]["synced"])
        self.assertTrue(rep["warmup"]["all_auth_ok"])
        self.assertTrue(rep["watch"]["events"])
        self.assertEqual(rep["enroll"], [])
        self.assertEqual(self.state.submit_count, 0)
        eng.close()

    def test_auto_with_confirm_runs_real_end_to_end(self):
        eng = lc.Engine(self.cfg(), records_path=self.rec())
        rep = eng.auto(confirm=True, cycles=1)
        self.assertTrue(rep["clock"]["synced"])
        self.assertTrue(rep["watch"]["events"])
        self.assertEqual(len(rep["enroll"]), 1)
        self.assertTrue(rep["enroll"][0]["success"])
        self.assertEqual(rep["enroll"][0]["reason"], "confirmed")
        self.assertEqual(self.state.submit_count, 1)
        eng.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
