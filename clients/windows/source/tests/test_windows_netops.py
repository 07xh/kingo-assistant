"""Offline tests for the network/time panel and its pure logic."""
from __future__ import annotations

import calendar
import email.message
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from core.netops import clock_offset, node_race, panel_model, session_guard


class NodeRaceTests(unittest.TestCase):
    def test_closed_port_returns_none_instead_of_raising(self):
        self.assertIsNone(node_race.tls_probe("127.0.0.1", port=1, timeout=0.3))

    def test_ranking_uses_median_and_drops_flaky_hosts(self):
        values = {
            "fast": [10.0, 12.0, 11.0],
            "slow": [90.0, 95.0, 100.0],
            "flaky": [8.0, None, None],
            "dead": [None, None, None],
        }

        def fake_probe(host, port=443, timeout=2.5, sni=None):
            queue = values[host]
            return queue.pop(0) if queue else None

        with patch("core.netops.node_race.tls_probe", side_effect=fake_probe):
            ranked = node_race.rank_hosts(
                ["slow", "fast", "flaky", "dead"], rounds=3, workers=4)

        self.assertEqual([row[0] for row in ranked], ["fast", "slow"])
        self.assertAlmostEqual(ranked[0][1], 11.0)
        self.assertEqual(ranked[0][2], 3)
        self.assertAlmostEqual(ranked[1][1], 95.0)

    def test_pick_respects_exclusions_and_threshold(self):
        ranked = [("a", 40.0, 3, 0), ("b", 120.0, 3, 0)]
        self.assertEqual(node_race.pick(ranked), ("a", 40.0))
        self.assertEqual(node_race.pick(ranked, exclude=("a",)), ("b", 120.0))
        self.assertIsNone(node_race.pick(ranked, exclude=("a",), max_ms=100.0))
        self.assertIsNone(node_race.pick([], max_ms=100.0))

    def test_stop_flag_returns_without_measuring(self):
        with patch("core.netops.node_race.tls_probe") as probe:
            ranked = node_race.rank_hosts(
                ["a", "b"], rounds=3, should_stop=lambda: True)
        probe.assert_not_called()
        self.assertEqual(ranked, [])

    def test_summary_mentions_best_host(self):
        text = node_race.summarize_ranked([("h", 42.0, 3, 0)])
        self.assertIn("h", text)
        self.assertIn("42", text)
        self.assertIn("没有可用节点", node_race.summarize_ranked([]))


class ClockOffsetTests(unittest.TestCase):
    def _headers(self, date_value):
        headers = email.message.Message()
        if date_value:
            headers["Date"] = date_value
        return headers

    def test_parses_http_date_header(self):
        headers = self._headers("Wed, 18 Nov 2026 13:35:00 GMT")
        expected = calendar.timegm((2026, 11, 18, 13, 35, 0, 0, 0, 0))
        self.assertAlmostEqual(clock_offset._server_time(headers), expected, 1)

    def test_reads_server_timing_epoch_when_present(self):
        headers = email.message.Message()
        headers["Server-Timing"] = "cache;desc=hit, date=1763472900.250"
        self.assertAlmostEqual(clock_offset._server_time(headers), 1763472900.25, 3)

    def test_missing_time_headers_yield_none(self):
        self.assertIsNone(clock_offset._server_time(self._headers(None)))

    def test_estimate_discards_outlier_rtt(self):
        samples = [(0.5, 0.05), (0.6, 0.06), (9.0, 5.0)]

        def fake_sample(url, timeout=3.0):
            return samples.pop(0)

        with patch("core.netops.clock_offset.sample_once", side_effect=fake_sample):
            result = clock_offset.estimate("https://example.test", samples=3)

        self.assertEqual(result["n"], 2)
        self.assertAlmostEqual(result["offset"], 0.55, 6)

    def test_failed_samples_report_no_offset(self):
        with patch("core.netops.clock_offset.sample_once",
                   return_value=(None, None)):
            result = clock_offset.estimate("https://example.test", samples=2)
        self.assertIsNone(result["offset"])
        self.assertEqual(result["n"], 0)

    def test_corrected_now_adds_offset_without_touching_system_clock(self):
        with patch("core.netops.clock_offset.time.time", return_value=1000.0):
            self.assertAlmostEqual(clock_offset.corrected_now(12.0), 1012.0)


class SessionGuardTests(unittest.TestCase):
    def test_classification_matrix(self):
        cases = (
            ((200, "", ""), session_guard.OK),
            ((200, '{"code":-1}', ""), session_guard.AUTH_LOST),
            ((200, "请登录后重试", ""), session_guard.AUTH_LOST),
            ((401, "", ""), session_guard.AUTH_LOST),
            ((403, "", ""), session_guard.AUTH_LOST),
            ((302, "", "https://x/passport/login"), session_guard.AUTH_LOST),
            ((302, "", "https://x/notice"), session_guard.OK),
            ((429, "", ""), session_guard.BUSY),
            ((503, "", ""), session_guard.BUSY),
            ((418, "", ""), session_guard.BUSY),
        )
        for args, expected in cases:
            with self.subTest(args=args):
                self.assertEqual(session_guard.classify_http(*args), expected)

    def test_transport_error_never_triggers_relogin(self):
        relogin = Mock(return_value=True)

        def probe():
            raise TimeoutError("connect timed out")

        guard = session_guard.SessionGuard(probe, relogin)
        self.assertEqual(guard.check_once(), session_guard.NETWORK)
        relogin.assert_not_called()
        self.assertEqual(guard.stats["network"], 1)

    def test_busy_response_backs_off_without_relogin(self):
        relogin = Mock(return_value=True)
        guard = session_guard.SessionGuard(
            lambda: (503, "", ""), relogin, interval=15)
        self.assertEqual(guard.check_once(), session_guard.BUSY)
        relogin.assert_not_called()

    def test_auth_loss_calls_relogin_once_and_records_result(self):
        relogin = Mock(return_value=True)
        events = []
        guard = session_guard.SessionGuard(
            lambda: (401, "", ""), relogin,
            on_event=lambda kind, message: events.append(kind))
        self.assertEqual(guard.check_once(), session_guard.AUTH_LOST)
        relogin.assert_called_once()
        self.assertEqual(guard.stats["relogin_ok"], 1)
        self.assertIn("auth_lost", events)
        self.assertIn("relogin_ok", events)

    def test_failed_relogin_is_counted_and_reported(self):
        guard = session_guard.SessionGuard(
            lambda: (401, "", ""), lambda: False)
        guard.check_once()
        self.assertEqual(guard.stats["relogin_fail"], 1)
        self.assertEqual(guard.stats["relogin_ok"], 0)

    def test_stats_text_is_stable(self):
        guard = session_guard.SessionGuard(lambda: (200, "", ""), lambda: True)
        guard.check_once()
        self.assertIn("检查 1", guard.stats_text())


class PanelModelTests(unittest.TestCase):
    def test_parse_hosts_handles_comments_ports_and_duplicates(self):
        text = (
            "# 候选节点\n"
            "10.0.0.1:443 主节点\n"
            "node.example.com  备注\n"
            "\n"
            "10.0.0.1\n"
            "2001:db8::1\n"
            "# 只有注释\n"
        )
        self.assertEqual(panel_model.parse_hosts(text),
                         ["10.0.0.1", "node.example.com", "2001:db8::1"])

    def test_parse_hosts_respects_limit_and_empty_input(self):
        self.assertEqual(panel_model.parse_hosts(""), [])
        text = "\n".join("h%d.example" % i for i in range(10))
        self.assertEqual(len(panel_model.parse_hosts(text, limit=3)), 3)

    def test_format_race_row_rounds_latency(self):
        self.assertEqual(panel_model.format_race_row(("h", 42.4, 3, 0)),
                         ("h", "42 ms", "3", "0"))

    def test_summarize_race_mentions_counts(self):
        text = panel_model.summarize_race([("h", 42.0, 3, 0)], 5)
        self.assertIn("h", text)
        self.assertIn("5", text)
        self.assertIn("全部不可用", panel_model.summarize_race([], 5))

    def test_offset_tone_thresholds(self):
        self.assertEqual(panel_model.offset_tone(None), "muted")
        self.assertEqual(panel_model.offset_tone(120), "ok")
        self.assertEqual(panel_model.offset_tone(-900), "warn")
        self.assertEqual(panel_model.offset_tone(2400), "danger")

    def test_offset_text_explains_direction(self):
        slower = panel_model.offset_text(
            {"offset": 0.5, "n": 7, "rtt_median": 0.1})
        faster = panel_model.offset_text(
            {"offset": -0.9, "n": 7, "rtt_median": 0.1})
        self.assertIn("本机慢", slower)
        self.assertIn("本机快", faster)
        self.assertIn("校准失败", panel_model.offset_text({"offset": None}))

    def test_badge_text_never_leaks_raw_floats(self):
        self.assertEqual(panel_model.offset_badge_text(None), "时钟未校准")
        self.assertIn("±250", panel_model.offset_badge_text(90))
        self.assertIn("600ms", panel_model.offset_badge_text(-600))

    def test_guard_text_reports_state_and_counts(self):
        stats = {"checks": 4, "auth_lost": 1, "relogin_ok": 1, "relogin_fail": 0}
        self.assertIn("运行中", panel_model.guard_text(stats, True))
        self.assertIn("已停止", panel_model.guard_text(stats, False))

    def test_mask_url_hides_query_and_token(self):
        masked = panel_model.mask_url(
            "https://jwmis.example.cn/a/b?token=secret#frag")
        self.assertNotIn("token", masked)
        self.assertNotIn("secret", masked)
        self.assertTrue(masked.startswith("https://jwmis.example.cn/a/b"))


try:
    import tkinter as tk
    _TK_IMPORT_ERROR = None
except Exception as exc:                             # pragma: no cover
    tk = None
    _TK_IMPORT_ERROR = exc


class NetOpsPanelWidgetTests(unittest.TestCase):
    """Real widget test; skipped when no display/Tk is available."""

    def setUp(self):
        if tk is None:
            self.skipTest("tkinter unavailable: %s" % (_TK_IMPORT_ERROR,))
        try:
            self.root = tk.Tk()
        except Exception as exc:                     # pragma: no cover
            self.skipTest("no Tk display: %s" % (exc,))
        self.root.withdraw()
        self.app = SimpleNamespace(log=Mock())
        from windows_netops_panel import build_netops_panel
        self.frame = tk.Frame(self.root)
        self.frame.pack()
        self.panel = build_netops_panel(self.app, self.frame,
                                        default_url="https://example.test")

    def tearDown(self):
        if getattr(self, "panel", None) is not None:
            self.panel.close()
        if getattr(self, "root", None) is not None:
            self.root.destroy()

    def test_panel_builds_offline_and_exposes_no_automatic_measurement(self):
        self.assertIs(self.app.netops, self.panel)
        self.assertEqual(self.panel.race_status.get()[:3], "尚未测")
        self.assertIn("未校准", self.panel.clock_status.get())
        self.assertIn("已停止", self.panel.guard_status.get())
        self.assertEqual(self.panel.url_var.get(), "https://example.test")
        self.assertTrue(self.panel.race_table.winfo_exists())
        self.assertIsNone(self.panel.get_offset())

    def test_race_results_land_in_the_table_and_status(self):
        self.panel._apply_race([("h1", 30.0, 3, 0), ("h2", 55.5, 2, 1)], 4)
        rows = self.panel.race_table.get_children()
        self.assertEqual(len(rows), 2)
        self.assertEqual(self.panel.race_table.item(rows[0], "values")[0], "h1")
        self.assertIn("h1", self.panel.race_status.get())
        self.assertEqual(str(self.panel.race_button.cget("state")), "normal")

    def test_clock_result_updates_offset_badge_and_status(self):
        self.panel._apply_clock({"offset": -0.9, "n": 5, "rtt_median": 0.12})
        self.assertAlmostEqual(self.panel.get_offset(), -0.9)
        self.assertIn("时钟偏差", self.panel.badge_var.get())
        self.assertIn("本机快", self.panel.clock_status.get())
        self.app.log.assert_called_once()

    def test_guard_toggle_refuses_without_http_target(self):
        self.panel.url_var.set("jwmis.example.cn")
        self.panel.toggle_guard()
        self.assertIsNone(self.panel.guard)
        self.assertIn("http", self.panel.guard_status.get())

    def test_close_is_idempotent(self):
        self.panel.close()
        self.panel.close()


if __name__ == "__main__":
    unittest.main()
