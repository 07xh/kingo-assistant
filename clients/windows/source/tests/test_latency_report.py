"""Offline tests for the latency report consumer.

Samples are synthesised in temporary files; nothing here touches a network.
"""
import json
import tempfile
import unittest
from pathlib import Path

from core.latency_report import (
    COMPONENT_PHASES,
    ReportError,
    build_run,
    compare_runs,
    format_comparison,
    format_run,
    latest_capture,
    load_records,
    main,
)


def row(kind="catalog", status=200, budget_wait=0.0, request=100.0,
        decode=2.0, parse=10.0, total=112.0, note="", at="2026-12-21T00:00:00+00:00"):
    return {"at": at, "kind": kind, "status": status,
            "budget_wait_ms": budget_wait, "request_ms": request,
            "decode_ms": decode, "parse_ms": parse, "total_ms": total,
            "bytes_read": 4096, "rows": 3, "note": note}


def write_capture(directory: Path, rows, name="latency.jsonl") -> Path:
    path = directory / name
    with path.open("w", encoding="utf-8") as stream:
        for item in rows:
            stream.write(json.dumps(item, ensure_ascii=False) + "\n")
    return path


class LoadTests(unittest.TestCase):
    def test_missing_file_is_an_error(self):
        with self.assertRaises(ReportError):
            load_records(Path("definitely-not-here.jsonl"))

    def test_directory_is_not_a_file(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ReportError):
                load_records(Path(directory))

    def test_malformed_lines_are_skipped(self):
        with tempfile.TemporaryDirectory() as directory:
            path = directory and Path(directory) / "latency.jsonl"
            path.write_text('{"kind":"catalog","total_ms":5}\nnot json\n\n'
                            '{"no_kind":1}\n', encoding="utf-8")
            self.assertEqual(len(load_records(path)), 1)

    def test_empty_capture_cannot_produce_a_run(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_capture(Path(directory), [])
            with self.assertRaises(ReportError):
                build_run(path)


class BuildTests(unittest.TestCase):
    def test_phase_statistics_and_bottleneck(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_capture(Path(directory), [
                row(budget_wait=1.0, request=100.0, decode=2.0, parse=8.0, total=111.0),
                row(budget_wait=1.0, request=120.0, decode=2.0, parse=8.0, total=131.0),
                row(budget_wait=1.0, request=140.0, decode=2.0, parse=8.0, total=151.0),
            ])
            run = build_run(path)
            self.assertEqual(run.samples, 3)
            self.assertEqual(run.bottleneck, "request_ms")
            self.assertEqual(run.phases["request_ms"]["p50"], 120.0)
            self.assertEqual(run.phases["request_ms"]["max"], 140.0)
            self.assertNotIn("total_ms", COMPONENT_PHASES)

    def test_kind_and_status_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_capture(Path(directory), [
                row(kind="catalog", status=200), row(kind="catalog", status=200),
                row(kind="identity", status=401),
            ])
            run = build_run(path)
            self.assertEqual(run.kinds, {"catalog": 2, "identity": 1})
            self.assertEqual(run.statuses, {"200": 2, "401": 1})

    def test_budget_share_exposes_local_throttling(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_capture(Path(directory), [
                row(budget_wait=80.0, request=20.0, total=100.0),
                row(budget_wait=80.0, request=20.0, total=100.0),
            ])
            run = build_run(path)
            self.assertAlmostEqual(run.budget_share_pct, 80.0, places=2)

    def test_budget_share_ignores_zero_total_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_capture(Path(directory), [
                row(budget_wait=0.0, request=100.0, total=0.0),
            ])
            self.assertEqual(build_run(path).budget_share_pct, 0.0)

    def test_retried_samples_are_counted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_capture(Path(directory), [
                row(), row(note="attempt=2"), row(note="entry_refresh"),
            ])
            self.assertEqual(build_run(path).retried_samples, 1)

    def test_time_range_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_capture(Path(directory), [
                row(at="2026-12-21T00:00:02+00:00"),
                row(at="2026-12-21T00:00:01+00:00"),
            ])
            run = build_run(path)
            self.assertEqual(run.first_at, "2026-12-21T00:00:01+00:00")
            self.assertEqual(run.last_at, "2026-12-21T00:00:02+00:00")

    def test_non_numeric_field_does_not_crash(self):
        with tempfile.TemporaryDirectory() as directory:
            path = directory and Path(directory) / "latency.jsonl"
            broken = row()
            broken["request_ms"] = "fast"
            path.write_text(json.dumps(broken) + "\n", encoding="utf-8")
            self.assertEqual(build_run(path).phases["request_ms"]["p50"], 0.0)


class FormatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def test_throttled_run_is_called_out(self):
        path = write_capture(self.directory, [row(budget_wait=90.0, total=100.0)])
        text = format_run(build_run(path))
        self.assertIn("本地请求配额", text)
        self.assertIn("主耗时阶段", text)

    def test_small_sample_warns_about_p95(self):
        path = write_capture(self.directory, [row()])
        self.assertIn("样本少于 50", format_run(build_run(path)))

    def test_parse_bottleneck_suggests_byte_prefilter(self):
        path = write_capture(self.directory, [
            row(request=10.0, decode=1.0, parse=200.0, total=212.0),
        ])
        self.assertIn("本地解析", format_run(build_run(path)))


class CompareTests(unittest.TestCase):
    def make(self, request_ms):
        path = write_capture(self.directory,
                             [row(request=request_ms, total=request_ms + 12.0)],
                             name=f"r{int(request_ms)}.jsonl")
        return build_run(path)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def test_faster_run_reports_improvement(self):
        result = compare_runs(self.make(200.0), self.make(100.0))
        self.assertLess(result["p50"]["change_pct"], 0)
        self.assertLess(result["p50"]["delta"], 0)
        self.assertIn("改善", format_comparison(result))

    def test_slower_run_reports_regression(self):
        result = compare_runs(self.make(100.0), self.make(200.0))
        self.assertGreater(result["p50"]["change_pct"], 0)
        self.assertIn("变差", format_comparison(result))

    def test_phase_can_be_selected_explicitly(self):
        result = compare_runs(self.make(200.0), self.make(100.0), phase="request_ms")
        self.assertEqual(result["phase"], "request_ms")
        self.assertEqual(result["p50"]["change_pct"], -50.0)

    def test_unknown_phase_is_rejected(self):
        with self.assertRaises(ReportError):
            compare_runs(self.make(100.0), self.make(100.0), phase="wall_clock")

    def test_zero_baseline_does_not_divide_by_zero(self):
        empty = write_capture(self.directory,
                              [row(request=0.0, decode=0.0, parse=0.0, total=0.0)],
                              name="zero.jsonl")
        result = compare_runs(build_run(empty), self.make(50.0))
        self.assertEqual(result["p50"]["change_pct"], 0.0)
        self.assertEqual(result["p50"]["before"], 0.0)


class DiscoveryTests(unittest.TestCase):
    def test_latest_capture_picks_newest_including_rotation(self):
        import os
        import time
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            old = write_capture(base, [row()], name="latency.jsonl.1")
            new = write_capture(base, [row()], name="latency.jsonl")
            os.utime(old, (1_600_000_000, 1_600_000_000))
            os.utime(new, (time.time(), time.time()))
            self.assertEqual(latest_capture(base).name, "latency.jsonl")

    def test_empty_directory_is_an_error(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ReportError):
                latest_capture(Path(directory))

    def test_missing_directory_is_an_error(self):
        with self.assertRaises(ReportError):
            latest_capture(Path("no-such-dir-xyz"))


class CliTests(unittest.TestCase):
    def test_text_report_exits_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_capture(Path(directory), [row()])
            self.assertEqual(main([str(path)]), 0)

    def test_json_report_exits_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            path = write_capture(Path(directory), [row()])
            self.assertEqual(main([str(path), "--json"]), 0)

    def test_broken_path_exits_nonzero(self):
        self.assertEqual(main(["definitely-not-here.jsonl"]), 2)

    def test_directory_argument_resolves_latest(self):
        with tempfile.TemporaryDirectory() as directory:
            write_capture(Path(directory), [row()])
            self.assertEqual(main([directory]), 0)


if __name__ == "__main__":
    unittest.main()
