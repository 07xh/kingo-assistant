"""Only synthetic offline tests. Never contact an actual school."""
import asyncio
import time
import unittest

import httpx

from core.client import KingoClient, RateLimitError
from core.desktop_settings import INTERVAL_PRESETS, parse_interval_range, update_config
from core.polling_policy import validate_rate_permission
from main import validate
from tests.test_local import config as sample_config


def permitted(seconds=0.5):
    config = sample_config()
    config["strategy"]["interval_range"] = [seconds, seconds]
    config["strategy"]["rate_permission"] = {
        "confirmed": True,
        "host": "campus.example.edu",
        "scope": "read-only-course-catalog",
        "minimum_interval_seconds": seconds,
        "evidence_ref": "FICTIONAL-OFFLINE-TEST-ONLY",
    }
    return config


class FastPolicyTest(unittest.TestCase):
    def test_named_presets_parse_without_float_drift(self):
        self.assertEqual(parse_interval_range("0.5", "0.5"), (0.5, 0.5))
        self.assertEqual(parse_interval_range("1", "1"), (1.0, 1.0))
        self.assertEqual(parse_interval_range("1.5", "1.5"), (1.5, 1.5))
        self.assertEqual(parse_interval_range("75", "135"), (75, 135))
        self.assertEqual(INTERVAL_PRESETS["极速（0.5 秒，需确认）"], (0.5, 0.5))
        for low, high in (("0.1", "1"), ("59", "60"), ("60.5", "120"),
                          ("1", "0.5"), ("NaN", "1"), ("６０", "120")):
            with self.subTest(low=low, high=high), self.assertRaises(ValueError):
                parse_interval_range(low, high)

    def test_default_unchanged_and_fast_denied_without_permission(self):
        normal = sample_config()
        self.assertEqual(validate_rate_permission(normal["strategy"], normal["base_url"]), (60, 90))
        normal["strategy"]["interval_range"] = [1, 1]
        with self.assertRaisesRegex(ValueError, "实验模式"):
            validate(normal, check_only=True)
        with self.assertRaises(ValueError):
            KingoClient(normal)

    def test_opt_in_allows_experimental_settings_without_claiming_school_approval(self):
        config = sample_config()
        config["strategy"]["interval_range"] = [0.5, 0.5]
        config["strategy"]["fast_polling_opt_in"] = True
        self.assertNotIn("rate_permission", config["strategy"])
        validate(config, check_only=True)
        self.assertEqual(validate_rate_permission(config["strategy"], config["base_url"]), (0.5, 0.5))
        # No opt-in should ever be interpreted as protocol verification.
        self.assertTrue(config["protocol"]["verified"])
        no_protocol = sample_config()
        no_protocol["protocol"]["verified"] = False
        no_protocol["strategy"].update({"interval_range": [1, 1],
                                        "fast_polling_opt_in": True})
        with self.assertRaisesRegex(ValueError, "尚未核对官方"):
            validate(no_protocol)

    def test_opt_in_cannot_override_known_stricter_permission(self):
        config = permitted(1)
        config["strategy"]["interval_range"] = [0.5, 0.5]
        config["strategy"]["fast_polling_opt_in"] = True
        with self.assertRaisesRegex(ValueError, "低于"):
            validate(config, check_only=True)

    def test_permission_host_scope_and_ceiling_guard(self):
        config = permitted(0.5)
        validate(config, check_only=True)
        self.assertEqual(validate_rate_permission(config["strategy"], config["base_url"]), (0.5, 0.5))
        for key, value in (("host", "another.example.edu"),
                           ("scope", "anything"),
                           ("confirmed", False),
                           ("evidence_ref", "")):
            changed = permitted()
            changed["strategy"]["rate_permission"][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate(changed, check_only=True)
        changed = permitted()
        changed["strategy"]["rate_permission"]["minimum_interval_seconds"] = 1
        with self.assertRaisesRegex(ValueError, "低于"):
            validate(changed, check_only=True)

    def test_gui_config_save_needs_approval(self):
        config = sample_config()
        with self.assertRaises(ValueError):
            update_config(config, courses=config["courses"], semester=config["xnxq"],
                          mode="monitor", target_time="2026-11-11 12:00:00",
                          interval_low=0.5, interval_high=0.5, max_cycles=10)


class RequestPacingTest(unittest.IsolatedAsyncioTestCase):
    async def test_half_second_enforced_for_every_request(self):
        timestamps = []
        def handler(request):
            timestamps.append(time.monotonic())
            return httpx.Response(200, text="OK")
        client = KingoClient(permitted(0.5))
        await client.http.aclose()
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            await client.read("GET", "/mine", "/")
            await client.read("GET", "/catalog", "/")
        finally:
            await client.aclose()
        self.assertEqual(len(timestamps), 2)
        self.assertGreaterEqual(timestamps[1] - timestamps[0], 0.47)

    async def test_429_stops_without_retry_even_in_fast_mode(self):
        hits = []
        def handler(request):
            hits.append(request)
            return httpx.Response(429, headers={"Retry-After": "60"})
        client = KingoClient(permitted(1))
        await client.http.aclose()
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            with self.assertRaises(RateLimitError):
                await client.read("GET", "/mine", "/")
        finally:
            await client.aclose()
        self.assertEqual(len(hits), 1)


if __name__ == "__main__":
    unittest.main()
