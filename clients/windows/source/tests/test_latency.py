"""Offline tests for latency instrumentation.

No test performs an external network request: the only socket call targets a
closed local port to assert the failure path, and HTTP clients are never built.
"""
import json
from pathlib import Path
import tempfile
import unittest

from core.latency import (
    DEFAULT_LATENCY_FILE, HandshakeProfile, LatencyRecorder, LatencySample,
    PHASES, handshake_profile, merge_summaries, percentiles, summarize_file,
)


class PercentileTests(unittest.TestCase):
    def test_empty_input_yields_zeros_instead_of_raising(self):
        report = percentiles([])
        self.assertEqual(report["count"], 0)
        for name in ("p50", "p90", "p95", "max", "mean"):
            self.assertEqual(report[name], 0.0)

    def test_single_sample_reports_same_value(self):
        report = percentiles([42.0])
        self.assertEqual(report["p50"], 42.0)
        self.assertEqual(report["p95"], 42.0)
        self.assertEqual(report["max"], 42.0)

    def test_percentiles_are_ordered_and_bounded(self):
        report = percentiles(list(range(1, 101)))
        self.assertLessEqual(report["p50"], report["p90"])
        self.assertLessEqual(report["p90"], report["p95"])
        self.assertLessEqual(report["p95"], report["max"])
        self.assertEqual(report["max"], 100.0)

    def test_non_numeric_entries_are_skipped(self):
        self.assertEqual(percentiles([None, 10.0])["count"], 1)


class SampleTests(unittest.TestCase):
    def test_negative_phase_is_rejected(self):
        with self.assertRaises(ValueError):
            LatencySample(kind="catalog", request_ms=-1.0)

    def test_invalid_status_is_rejected(self):
        with self.assertRaises(ValueError):
            LatencySample(kind="catalog", status=99)

    def test_blank_kind_is_rejected(self):
        with self.assertRaises(ValueError):
            LatencySample(kind="  ")

    def test_mapping_rounds_and_keeps_all_phases(self):
        mapping = LatencySample(kind="catalog", status=200, budget_wait_ms=1.23456,
                                request_ms=2.34567, total_ms=3.45678).to_mapping()
        for name in PHASES:
            self.assertIn(name, mapping)
        self.assertEqual(mapping["budget_wait_ms"], 1.235)
        self.assertEqual(mapping["status"], 200)

    def test_personal_identifiers_are_not_part_of_the_record(self):
        mapping = LatencySample(kind="catalog", rows=3).to_mapping()
        serialized = json.dumps(mapping).lower()
        for banned in ("course_id", "class_id", "cookie", "student"):
            self.assertNotIn(banned, serialized)


class RecorderTests(unittest.TestCase):
    def test_samples_are_appended_as_jsonl(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / DEFAULT_LATENCY_FILE
            recorder = LatencyRecorder(path)
            recorder.record(LatencySample(kind="catalog", request_ms=10.0,
                                          total_ms=12.0))
            recorder.record(LatencySample(kind="identity", request_ms=20.0,
                                          total_ms=22.0))
            lines = [line for line in path.read_text(encoding="utf-8").splitlines()
                     if line.strip()]
            self.assertEqual(len(lines), 2)
            self.assertEqual(json.loads(lines[0])["kind"], "catalog")

    def test_disabled_recorder_still_accumulates_in_memory(self):
        recorder = LatencyRecorder(None)
        self.assertFalse(recorder.enabled)
        recorder.record(LatencySample(kind="catalog"))
        self.assertEqual(recorder.summary()["samples"], 1)

    def test_size_cap_rotates_instead_of_growing_forever(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / DEFAULT_LATENCY_FILE
            recorder = LatencyRecorder(path, max_bytes=200)
            for _ in range(20):
                recorder.record(LatencySample(kind="catalog", request_ms=1.0))
            self.assertTrue(path.with_suffix(path.suffix + ".1").exists())

    def test_summary_identifies_the_bottleneck_phase(self):
        recorder = LatencyRecorder(None)
        for value in (5.0, 6.0, 7.0):
            recorder.record(LatencySample(kind="catalog", budget_wait_ms=value,
                                          request_ms=100.0, total_ms=110.0))
        self.assertEqual(recorder.summary()["bottleneck"], "request_ms")

    def test_summary_filters_by_kind(self):
        recorder = LatencyRecorder(None)
        recorder.record(LatencySample(kind="catalog"))
        recorder.record(LatencySample(kind="identity"))
        self.assertEqual(recorder.summary(kind="identity")["samples"], 1)
        self.assertEqual(recorder.summary()["kinds"],
                         {"catalog": 1, "identity": 1})

    def test_report_shape_is_gui_safe(self):
        recorder = LatencyRecorder(None)
        recorder.record(LatencySample(kind="catalog"))
        report = recorder.report()
        for key in ("file", "enabled", "last_error", "overall", "by_kind"):
            self.assertIn(key, report)

    def test_summarize_file_ignores_corrupt_lines(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / DEFAULT_LATENCY_FILE
            path.write_text('{"kind":"catalog","request_ms":5,"total_ms":6}\n'
                            'not json\n\n', encoding="utf-8")
            self.assertEqual(summarize_file(path)["samples"], 1)


class HandshakeTests(unittest.TestCase):
    def test_invalid_target_is_rejected(self):
        with self.assertRaises(ValueError):
            handshake_profile("")
        with self.assertRaises(ValueError):
            handshake_profile("example.invalid", port=0)

    def test_closed_local_port_reports_error_without_raising(self):
        profile = handshake_profile("127.0.0.1", 1, timeout=0.2)
        self.assertIsInstance(profile, HandshakeProfile)
        self.assertTrue(profile.error)
        self.assertEqual(profile.host, "127.0.0.1")
        self.assertIn("error", profile.as_dict())


class MergeTests(unittest.TestCase):
    def test_merge_is_conservative_on_p95(self):
        left = {"samples": 2, "kinds": {"catalog": 2},
                "phases": {name: {"count": 2, "p50": 10.0, "p90": 10.0,
                                  "p95": 10.0, "max": 10.0, "mean": 10.0}
                           for name in PHASES}}
        right = {"samples": 3, "kinds": {"catalog": 3},
                 "phases": {name: {"count": 3, "p50": 20.0, "p90": 20.0,
                                   "p95": 30.0, "max": 30.0, "mean": 20.0}
                            for name in PHASES}}
        merged = merge_summaries([left, right])
        self.assertEqual(merged["samples"], 5)
        self.assertEqual(merged["phases"]["total_ms"]["p95"], 30.0)
        self.assertEqual(merged["phases"]["total_ms"]["p50"], 15.0)

    def test_merge_of_nothing_is_empty(self):
        self.assertEqual(merge_summaries([])["samples"], 0)


if __name__ == "__main__":
    unittest.main()
