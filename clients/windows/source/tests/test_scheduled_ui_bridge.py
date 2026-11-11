"""Offline Windows admission bridge tests; never contact school endpoints."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime
import unittest

from core.enrollment import EnrollmentNotReady
from core.official_portal_schedule import CHINA_TIMEZONE
from core.scheduled_ui_bridge import prepare_scheduled_plan, run_prepared_schedule


class ScheduledUiBridgeTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 11, 16, 0, tzinfo=CHINA_TIMEZONE).timestamp()
        self.config = {
            "xnxq": "2026-2027-2",
            "strategy": {
                "target_time": "2026-12-21 09:00:00",
                "window_poll_interval_range": [60, 120],
                "window_max_wait_minutes": 60,
            },
            "courses": [
                {"key": "A", "course_id": "SYNTHETIC-101", "class_id": "K1", "group": "A"},
                {"key": "B", "course_id": "SYNTHETIC-102", "class_id": "K2", "group": "A"},
            ],
        }

    def test_plan_is_typed_and_preserves_priority_without_network(self):
        plan = prepare_scheduled_plan(self.config, now_epoch=self.now)
        self.assertEqual(plan.target_time, "2026-12-21 09:00:00")
        self.assertEqual([x.key for x in plan.targets], ["A", "B"])
        self.assertEqual([x.class_id for x in plan.targets], ["K1", "K2"])
        self.assertEqual(plan.poll_interval_range, (60, 120))
        self.assertEqual(plan.max_wait_minutes, 60)

    def test_placeholder_and_missing_teaching_class_fail_closed(self):
        for field, value in (("course_id", "FILL_FROM_OFFICIAL_PAGE"),
                             ("class_id", ""), ("class_id", None)):
            config = deepcopy(self.config)
            config["courses"][0][field] = value
            with self.subTest(field=field, value=value):
                with self.assertRaises(ValueError):
                    prepare_scheduled_plan(config, now_epoch=self.now)

    def test_duplicate_exact_class_fails_closed(self):
        config = deepcopy(self.config)
        config["courses"][1]["course_id"] = "SYNTHETIC-101"
        config["courses"][1]["class_id"] = "K1"
        with self.assertRaises(ValueError):
            prepare_scheduled_plan(config, now_epoch=self.now)

    def test_invalid_window_poll_limits_fail_before_scheduling(self):
        for invalid in ([30, 60], [60, 3601], [120, 60], ["60", 60]):
            config = deepcopy(self.config)
            config["strategy"]["window_poll_interval_range"] = invalid
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    prepare_scheduled_plan(config, now_epoch=self.now)

    def test_real_engine_missing_capabilities_never_enters_wait(self):
        plan = prepare_scheduled_plan(self.config, now_epoch=self.now)

        class UnverifiedEngine:
            def __init__(self):
                self.checked = 0

            def validate_ready(self):
                self.checked += 1
                raise EnrollmentNotReady("no official adapter")

        engine = UnverifiedEngine()
        with self.assertRaises(EnrollmentNotReady):
            asyncio.run(run_prepared_schedule(plan, engine))
        self.assertEqual(engine.checked, 1)


if __name__ == "__main__":
    unittest.main()
