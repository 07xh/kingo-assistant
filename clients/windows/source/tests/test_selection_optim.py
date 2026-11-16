"""Offline tests for the shared selection-strategy layer.

Validates the client-side port of the research optimisations plus the new
peak-window polling schedule. No university endpoint is contacted.
"""
import datetime
from dataclasses import dataclass
import time
import unittest

from core.course_models import CourseOffering
from core.selection_optim import (
    ABSOLUTE_MIN_INTERVAL, ClockSync, Decision, FailoverPlanner,
    FailureClassifier, PeakWindow, PollingSchedule, ReleaseMonitor,
    offering_fingerprint, parse_server_time,
)


class PeakWindowTests(unittest.TestCase):
    def test_window_containing_a_moment(self):
        window = PeakWindow(None, "08:00", "09:30", "补选首日")
        moment = datetime.datetime(2026, 12, 21, 8, 45)
        self.assertTrue(window.contains(moment))
        self.assertFalse(window.contains(moment.replace(hour=10)))

    def test_window_wrapping_past_midnight(self):
        window = PeakWindow(None, "23:00", "01:00")
        self.assertTrue(window.contains(datetime.datetime(2026, 12, 21, 23, 30)))
        self.assertTrue(window.contains(datetime.datetime(2026, 12, 22, 0, 30)))
        self.assertFalse(window.contains(datetime.datetime(2026, 12, 22, 2, 0)))

    def test_weekday_filter_is_honoured(self):
        window = PeakWindow(0, "08:00", "09:00")   # Monday
        monday = datetime.datetime(2026, 12, 21, 8, 30)
        self.assertEqual(monday.weekday(), 0)
        self.assertTrue(window.contains(monday))
        self.assertFalse(window.contains(monday + datetime.timedelta(days=1)))

    def test_invalid_fields_are_rejected(self):
        for args in ((7, "08:00", "09:00"), (None, "8:00", "09:00"),
                     (None, "25:00", "09:00"), (None, "08:00", "09:99")):
            with self.subTest(args=args):
                with self.assertRaises(ValueError):
                    PeakWindow(*args)


class PollingScheduleTests(unittest.TestCase):
    def test_interval_follows_peak_window(self):
        schedule = PollingSchedule(peak_interval=30, offpeak_interval=120,
                                   windows=[PeakWindow(None, "08:00", "09:00")])
        peak = datetime.datetime(2026, 12, 21, 8, 30)
        off = datetime.datetime(2026, 12, 21, 13, 0)
        self.assertEqual(schedule.interval(peak), 30.0)
        self.assertEqual(schedule.interval(off), 120.0)

    def test_hard_floor_cannot_be_under_cut(self):
        with self.assertRaises(ValueError):
            PollingSchedule(peak_interval=5, offpeak_interval=60)
        with self.assertRaises(ValueError):
            PollingSchedule(peak_interval=30, offpeak_interval=10)

    def test_peak_may_not_exceed_offpeak(self):
        with self.assertRaises(ValueError):
            PollingSchedule(peak_interval=120, offpeak_interval=60)

    def test_absurd_interval_is_rejected(self):
        with self.assertRaises(ValueError):
            PollingSchedule(peak_interval=30, offpeak_interval=7200)

    def test_disabled_pickup_keeps_existing_interval_range(self):
        schedule = PollingSchedule.from_config(
            {"interval_range": [60, 90], "pickup": {"enabled": False}})
        self.assertEqual(schedule.windows, [])
        self.assertEqual(schedule.interval(), 90.0)

    def test_enabled_pickup_reads_windows_from_config(self):
        schedule = PollingSchedule.from_config({
            "interval_range": [60, 60],
            "pickup": {"enabled": True, "peak_interval_seconds": 30,
                       "offpeak_interval_seconds": 90,
                       "peak_windows": [{"weekday": 0, "start": "08:00",
                                         "end": "09:00", "label": "首日"}]},
        })
        self.assertEqual(len(schedule.windows), 1)
        moment = datetime.datetime(2026, 12, 21, 8, 10)
        self.assertEqual(schedule.interval(moment), 30.0)

    def test_malformed_window_config_is_an_error(self):
        with self.assertRaises(ValueError):
            PollingSchedule.from_config(
                {"pickup": {"enabled": True, "peak_windows": ["08:00"]}})

    def test_report_exposes_the_floor(self):
        report = PollingSchedule().report()
        self.assertEqual(report["floor_s"], ABSOLUTE_MIN_INTERVAL)


@dataclass
class _DuckOffering:
    course_id: str
    class_id: str
    remain: int | None

    def fingerprint(self) -> str:
        return f"{self.course_id}|{self.class_id}"


class OfferingFingerprintTests(unittest.TestCase):
    def test_client_model_has_no_fingerprint_method_but_still_works(self):
        offering = CourseOffering("C1", "K1", 3)
        self.assertFalse(hasattr(offering, "fingerprint"))
        self.assertEqual(offering_fingerprint(offering), "C1\x1fK1")

    def test_duck_typed_offering_uses_its_own_fingerprint(self):
        self.assertEqual(offering_fingerprint(_DuckOffering("C1", "K2", 1)),
                         "C1|K2")


class ReleaseMonitorTests(unittest.TestCase):
    def test_new_reopened_and_increased_are_distinguished(self):
        monitor = ReleaseMonitor()
        self.assertEqual(monitor.observe([CourseOffering("C1", "K1", 0)]), [])
        opened = monitor.observe([CourseOffering("C1", "K1", 2)])
        self.assertEqual([e.kind for e in opened], ["reopened"])
        increased = monitor.observe([CourseOffering("C1", "K1", 5)])
        self.assertEqual([e.kind for e in increased], ["increased"])
        appeared = monitor.observe([CourseOffering("C1", "K1", 5),
                                    CourseOffering("C2", "K9", 1)])
        self.assertEqual([e.kind for e in appeared], ["new"])

    def test_stable_or_shrinking_poll_emits_nothing(self):
        monitor = ReleaseMonitor()
        monitor.observe([CourseOffering("C1", "K1", 4)])
        self.assertEqual(monitor.observe([CourseOffering("C1", "K1", 4)]), [])
        self.assertEqual(monitor.observe([CourseOffering("C1", "K1", 1)]), [])
        self.assertEqual(monitor.observe([CourseOffering("C1", "K1", 0)]), [])

    def test_report_counts(self):
        monitor = ReleaseMonitor()
        monitor.observe([CourseOffering("C1", "K1", 1)])
        monitor.observe([CourseOffering("C1", "K1", 3)])
        report = monitor.report()
        self.assertEqual(report["polls"], 2)
        self.assertEqual(report["events"], 2)
        self.assertEqual(report["kinds"], {"new": 1, "reopened": 0,
                                           "increased": 1})

    def test_unknown_remain_is_treated_as_zero_not_as_available(self):
        monitor = ReleaseMonitor()
        self.assertEqual(monitor.observe([CourseOffering("C1", "K1", None)]), [])


class ClockTests(unittest.TestCase):
    def test_date_header_parses_to_epoch(self):
        epoch = parse_server_time("Mon, 21 Dec 2026 00:00:00 GMT", "Date")
        self.assertIsNotNone(epoch)
        self.assertGreater(epoch, 1_700_000_000)

    def test_custom_millisecond_header_is_precise(self):
        epoch = parse_server_time("1766275200123", "X-Server-Time")
        self.assertAlmostEqual(epoch, 1766275200.123, places=3)

    def test_garbage_header_returns_none(self):
        self.assertIsNone(parse_server_time("not a date", "Date"))

    def test_wait_until_returns_alignment_error(self):
        clock = ClockSync()
        target = time.time() + 0.05
        result = clock.wait_until(target, lead_ms=0.0, max_wait_s=5.0)
        self.assertTrue(result["aligned"])
        self.assertLess(result["error_ms"], 100.0)

    def test_wait_until_respects_max_wait(self):
        clock = ClockSync()
        result = clock.wait_until(time.time() + 60, lead_ms=0.0, max_wait_s=0.01)
        self.assertFalse(result["aligned"])
        self.assertEqual(result["reason"], "max_wait_exceeded")


class ClassifierTests(unittest.TestCase):
    def setUp(self):
        self.classifier = FailureClassifier({})

    def test_transport_failure_halts(self):
        self.assertEqual(self.classifier.classify(0, b"", False,
                                                  "timeout").action, "halt")

    def test_rate_limit_cools_down(self):
        self.assertEqual(self.classifier.classify(429, b"", False).action,
                         "cooldown")

    def test_full_marker_moves_to_next_without_retry(self):
        decision = self.classifier.classify(200, "名额已满".encode("utf-8"), False)
        self.assertEqual(decision.action, "next")

    def test_window_marker_waits(self):
        decision = self.classifier.classify(
            200, "当前时间不是有效的选课时间区段".encode("utf-8"), False)
        self.assertEqual(decision.action, "wait")

    def test_confirmed_readback_stops(self):
        self.assertEqual(self.classifier.classify(200, b"", True).action, "stop")

    def test_unverified_readback_halts_instead_of_retrying(self):
        self.assertEqual(self.classifier.classify(200, b"", False).action, "halt")


class FailoverTests(unittest.TestCase):
    def test_exact_class_match_required(self):
        offers = [CourseOffering("C1", "K1", 1), CourseOffering("C1", "K2", 1)]
        self.assertIsNone(FailoverPlanner.match(offers, {"course_id": "C1",
                                                         "class_id": "K3"}))
        matched = FailoverPlanner.match(offers, {"course_id": "C1",
                                                 "class_id": "K2"})
        self.assertEqual(matched.class_id, "K2")

    def test_order_is_priority_then_identity(self):
        planner = FailoverPlanner([
            {"course_id": "C2", "class_id": "K1", "priority": 2},
            {"course_id": "C1", "class_id": "K1", "priority": 1},
            {"course_id": "C3", "class_id": "K1", "priority": 3, "enabled": False},
        ])
        self.assertEqual(len(planner), 2)
        self.assertEqual(planner.first()["course_id"], "C1")
        self.assertEqual(planner.next_after({"course_id": "C1",
                                             "class_id": "K1"})["course_id"], "C2")

    def test_note_records_decision(self):
        planner = FailoverPlanner([{"course_id": "C1", "class_id": "K1"}])
        planner.note({"course_id": "C1", "class_id": "K1"},
                     Decision("business_full", "next", "marker"), None)
        self.assertEqual(planner.report()["tried"], 1)


if __name__ == "__main__":
    unittest.main()
