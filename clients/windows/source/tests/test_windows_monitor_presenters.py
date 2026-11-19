"""Monitor-event presentation stays on Tk thread and preserves old meaning."""
import queue
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from core.enrollment import RunReport
from core.enrollment_schedule import ScheduledRunReport
from windows_gui import Application, MonitorEvent
from windows_monitor_events import (
    apply_monitor_status, format_adaptive_monitor_status, format_official_probe_results,
    scheduled_reason_text,
)


class StatusPresenterTests(TestCase):
    def test_detects_change_once_and_updates_only_visible_rows(self):
        table = Mock()
        table.exists.side_effect = lambda key: key == "A"
        app = SimpleNamespace(
            _previous_states={"A": "full"},
            status=Mock(), log=Mock(), bell=Mock(), table=table,
        )
        observed = []
        def detector(old, new):
            observed.append((dict(old), dict(new)))
            return ["A"] if old.get("A") == "full" else []

        payload = (3, {"A": "available", "B": "unknown"}, 45.1, 0.9)
        apply_monitor_status(app, payload, change_detector=detector)
        self.assertEqual(observed, [
            ({"A": "full"}, {"A": "available", "B": "unknown"}),
        ])
        app.status.set.assert_called_once_with(
            "只读监测｜查询 3 次｜最近请求 45 ms｜解析 0.9 ms｜请以官网为准")
        app.bell.assert_called_once()
        self.assertIn("不代表选课成功", app.log.call_args.args[0])
        self.assertEqual(app._previous_states, payload[1])
        table.set.assert_called_once_with("A", "status", "available")

        app.log.reset_mock()
        app.bell.reset_mock()
        apply_monitor_status(app, payload, change_detector=detector)
        app.bell.assert_not_called()
        app.log.assert_not_called()

    def test_empty_status_has_no_announcement(self):
        app = SimpleNamespace(
            _previous_states={}, status=Mock(), bell=Mock(),
            log=Mock(), table=Mock(),
        )
        apply_monitor_status(
            app, (0, {}, 0, 0), change_detector=lambda old, new: [])
        app.bell.assert_not_called()
        app.log.assert_not_called()
        self.assertEqual(app._previous_states, {})


class AdaptiveStatusPresenterTests(TestCase):
    def test_disabled_and_missing_values(self):
        self.assertIn("关闭", format_adaptive_monitor_status({"enabled": False}))
        self.assertIn("关闭", format_adaptive_monitor_status(None))

    def test_enabled_anonymized_report(self):
        result = format_adaptive_monitor_status({
            "enabled": True, "slowdown_multiplier": 2.0,
            "slow_samples": 1, "healthy_samples": 3,
            "session_cookie": "SHOULD_NOT_APPEAR", "course_id": "SECRET_COURSE",
        })
        self.assertIn("放缓 2×", result)
        self.assertIn("压力 1 次", result)
        self.assertNotIn("SHOULD_NOT_APPEAR", result)
        self.assertNotIn("SECRET_COURSE", result)

    def test_malformed_enabled_report_fails_closed(self):
        self.assertIn("不可用", format_adaptive_monitor_status({
            "enabled": True, "slowdown_multiplier": "2",
            "slow_samples": 1, "healthy_samples": 1,
        }))


class ProbeTextTests(TestCase):
    def test_probe_keeps_official_names_and_no_speed_claim(self):
        entries = [SimpleNamespace(identifier="main", name="华水教务登录页")]
        results = [
            SimpleNamespace(entry_id="main", state=SimpleNamespace(value="reachable"),
                            http_status=200, elapsed_ms=32.4),
            SimpleNamespace(entry_id="unknown", state=SimpleNamespace(value="failed"),
                            http_status=None, elapsed_ms=None),
        ]
        rendered = format_official_probe_results(results, entries=entries)
        self.assertIn("华水教务登录页：reachable / HTTP 200 / 32 ms", rendered)
        self.assertIn("未知入口：failed / HTTP — / —", rendered)
        self.assertIn("不能用于证明选课后端速度或名额", rendered)


class ScheduledReasonPresenterTests(TestCase):
    def test_former_user_messages_are_preserved(self):
        self.assertEqual(
            scheduled_reason_text("window_unknown"),
            "无法确认官方开放状态｜已停止")
        self.assertEqual(
            scheduled_reason_text("engine_halted"),
            "选课引擎保护性停止｜请核对官方已选记录")
        self.assertEqual(
            scheduled_reason_text("unrecognized"),
            "自动选课任务已结束｜结果待确认")

    def test_old_poll_method_uses_presenter_and_does_not_claim_write(self):
        app = SimpleNamespace(
            _closing=False, worker=None, scheduled_worker=object(),
            events=queue.Queue(),
            _tick_portal_appointment=Mock(),
            status=Mock(), log=Mock(), after=Mock(), poll=Mock(),
        )
        app.events.put(MonitorEvent(
            "scheduled_result", ScheduledRunReport(
                "window_unknown",
                RunReport({"A": "blocked"}, True), 1, 0.0,
            ),
        ))
        Application.poll(app)
        app.status.set.assert_called_once_with(
            "无法确认官方开放状态｜已停止")
        self.assertIn("请以真实教务记录为准", app.log.call_args.args[0])
        app.after.assert_called_once_with(300, app.poll)


if __name__ == "__main__":
    import unittest
    unittest.main()
