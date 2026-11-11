"""Offline-only official-portal appointment tests: no school HTTP."""
from datetime import datetime
import unittest

from core.official_portal_schedule import (
    CHINA_TIMEZONE, OfficialPortalAppointment, format_countdown,
    prepare_appointment,
)


class PortalScheduleTest(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 11, 16, 0, tzinfo=CHINA_TIMEZONE).timestamp()
        self.config = {
            "strategy": {"target_time": "2026-12-21 09:00:00"},
            "courses": [
                {"key": "A", "course_id": "C101", "class_id": "T1"},
                {"key": "B", "course_name": "一门合成课程"},
            ],
        }

    def test_china_local_time_is_used_independently_of_device_timezone(self):
        appointment = prepare_appointment(self.config, now_epoch=self.now)
        self.assertIsInstance(appointment, OfficialPortalAppointment)
        expected = datetime(2026, 12, 21, 9, 0, 0, tzinfo=CHINA_TIMEZONE)
        self.assertEqual(appointment.start_at_epoch, expected.timestamp())
        self.assertEqual(appointment.target_count, 2)
        self.assertFalse(appointment.is_due(self.now))
        self.assertTrue(appointment.is_due(appointment.start_at_epoch))
        self.assertEqual(appointment.seconds_remaining(appointment.start_at_epoch), 0)

    def test_invalid_and_unconfirmed_time_never_create_appointment(self):
        for value in ("", "2026-12-21", "2026/12/21 09:00:00",
                      "2026-12-21 9:00:00", "2026-02-30 09:00:00",
                      "not a date"):
            self.config["strategy"]["target_time"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                prepare_appointment(self.config, now_epoch=self.now)

    def test_past_and_far_future_dates_are_rejected(self):
        for value in ("2026-11-10 08:00:00", "2027-09-01 08:00:00"):
            self.config["strategy"]["target_time"] = value
            with self.subTest(value=value), self.assertRaises(ValueError):
                prepare_appointment(self.config, now_epoch=self.now)

    def test_template_target_ids_not_mistaken_for_real_courses(self):
        self.config["courses"] = [
            {"key": "A", "course_id": "FILL_FROM_OFFICIAL_PAGE"},
            {"key": "B", "course_id": "SECOND_OPTION_ID"},
        ]
        with self.assertRaisesRegex(ValueError, "至少填写"):
            prepare_appointment(self.config, now_epoch=self.now)

    def test_course_name_only_is_valid_for_manual_official_search(self):
        self.config["courses"] = [{"key": "A", "course_name": "合成课程"}]
        report = prepare_appointment(self.config, now_epoch=self.now)
        self.assertEqual(report.target_count, 1)

    def test_countdown_format(self):
        self.assertEqual(format_countdown(0), "00:00:00")
        self.assertEqual(format_countdown(90), "00:01:30")
        self.assertEqual(format_countdown(86401), "1天 00:00:01")
        with self.assertRaises(ValueError):
            format_countdown(-1)


if __name__ == "__main__":
    unittest.main()
