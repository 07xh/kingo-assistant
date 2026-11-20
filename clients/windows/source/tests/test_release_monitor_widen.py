"""Offline tests for release-monitor widening and bounded event history.

``ReleaseMonitor.widen_suggestions()`` answers the only question that matters
when the configured class is full: "which other class of THIS course has a seat
right now?" It must never cross courses, never count UNKNOWN remain as a seat,
and never suggest a class the caller already holds.
"""
import unittest
from dataclasses import dataclass

from core.selection_optim import ReleaseMonitor


@dataclass
class Offer:
    course_id: str
    class_id: str
    remain: object = 0
    course_name: str = ""
    teacher: str = ""


class WidenTests(unittest.TestCase):
    def setUp(self):
        self.monitor = ReleaseMonitor()
        self.monitor.observe([
            Offer("K001", "C1", 0),
            Offer("K001", "C2", 3),
            Offer("K001", "C3", 1),
            Offer("K001", "C4", None),      # UNKNOWN is not a seat
            Offer("K002", "C9", 8),         # different course must not leak in
        ])

    def test_sorted_by_remaining_descending(self):
        rows = self.monitor.widen_suggestions("K001")
        self.assertEqual([row["class_id"] for row in rows], ["C2", "C3"])
        self.assertEqual([row["remain"] for row in rows], [3, 1])

    def test_unknown_remain_is_never_suggested(self):
        rows = self.monitor.widen_suggestions("K001")
        self.assertNotIn("C4", [row["class_id"] for row in rows])

    def test_other_course_never_leaks(self):
        rows = self.monitor.widen_suggestions("K001")
        self.assertTrue(all(row["course_id"] == "K001" for row in rows))

    def test_excluded_class_is_skipped(self):
        rows = self.monitor.widen_suggestions("K001", exclude_class_ids=["C2"])
        self.assertEqual([row["class_id"] for row in rows], ["C3"])

    def test_limit_is_honoured(self):
        rows = self.monitor.widen_suggestions("K001", limit=1)
        self.assertEqual(len(rows), 1)

    def test_min_remain_filters(self):
        rows = self.monitor.widen_suggestions("K001", min_remain=2)
        self.assertEqual([row["class_id"] for row in rows], ["C2"])

    def test_unknown_course_returns_nothing(self):
        self.assertEqual(self.monitor.widen_suggestions("K999"), [])

    def test_invalid_arguments_are_rejected(self):
        for kwargs in ({"course_id": ""}, {"course_id": "K001", "limit": 0},
                       {"course_id": "K001", "limit": 51}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    self.monitor.widen_suggestions(**kwargs)

    def test_row_shape_is_json_safe(self):
        row = self.monitor.widen_suggestions("K001")[0]
        self.assertEqual(set(row), {"course_id", "class_id", "remain",
                                    "course_name", "teacher"})


class AvailableNowTests(unittest.TestCase):
    def test_only_positive_integer_remain_counts(self):
        monitor = ReleaseMonitor()
        monitor.observe([
            Offer("K1", "A", 2), Offer("K1", "B", 0),
            Offer("K1", "C", None), Offer("K1", "D", 5),
        ])
        self.assertEqual([o.class_id for o in monitor.available_now()],
                         ["D", "A"])

    def test_empty_before_any_poll(self):
        self.assertEqual(ReleaseMonitor().available_now(), [])

    def test_min_remain_must_be_positive(self):
        with self.assertRaises(ValueError):
            ReleaseMonitor().available_now(min_remain=0)


class EventHistoryTests(unittest.TestCase):
    def test_history_is_bounded_and_counts_drops(self):
        monitor = ReleaseMonitor(max_events=16)
        for index in range(40):
            monitor.observe([Offer("K1", f"C{index}", 1)])
        self.assertEqual(len(monitor.events), 16)
        self.assertEqual(monitor.dropped_events, 24)
        report = monitor.report()
        self.assertEqual(report["max_events"], 16)
        self.assertEqual(report["dropped_events"], 24)

    def test_newest_events_survive(self):
        monitor = ReleaseMonitor(max_events=16)
        for index in range(40):
            monitor.observe([Offer("K1", f"C{index}", 1)])
        self.assertEqual(monitor.events[-1].class_id, "C39")

    def test_invalid_max_events(self):
        for value in (0, 15, 100_001, "16", 16.0):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    ReleaseMonitor(max_events=value)

    def test_default_is_large_enough_for_a_whole_window(self):
        self.assertEqual(ReleaseMonitor().max_events, 2000)


if __name__ == "__main__":
    unittest.main()
