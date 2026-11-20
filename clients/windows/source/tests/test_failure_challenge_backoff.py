"""Offline tests for challenge detection and bounded retry backoff.

A human challenge (CAPTCHA / slider / SMS / CAS re-auth) must NEVER be classified
as a transient error: retrying a challenge burns attempts against a counter that
locks the account. These tests pin that distinction, plus the backoff ladder that
only applies to idempotent reads.
"""
import unittest

from core.selection_optim import (
    DEFAULT_CHALLENGE_MARKERS, FailureClassifier, RetryBackoff,
)


def fixed_jitter(value: float):
    return lambda: value


class ChallengeTests(unittest.TestCase):
    def setUp(self):
        self.classifier = FailureClassifier({})

    def test_chinese_captcha_body_halts(self):
        decision = self.classifier.classify(200, "请输入验证码".encode("utf-8"), False)
        self.assertEqual(decision.kind, "challenge_required")
        self.assertEqual(decision.action, "halt")

    def test_slider_marker_halts(self):
        decision = self.classifier.classify(
            200, "请拖动滑块完成验证".encode("utf-8"), False)
        self.assertEqual(decision.kind, "challenge_required")
        self.assertEqual(decision.action, "halt")

    def test_sms_marker_halts(self):
        decision = self.classifier.classify(
            200, "请输入短信验证码".encode("utf-8"), False)
        self.assertEqual(decision.kind, "challenge_required")

    def test_english_captcha_marker_halts(self):
        decision = self.classifier.classify(
            200, b"<html>Please solve the CAPTCHA</html>", False)
        self.assertEqual(decision.kind, "challenge_required")

    def test_cas_marker_halts(self):
        decision = self.classifier.classify(
            200, "统一身份认证 已过期".encode("utf-8"), False)
        self.assertEqual(decision.kind, "challenge_required")

    def test_challenge_beats_403(self):
        """403 + CAPTCHA is a solvable challenge, not an auth failure."""
        decision = self.classifier.classify(403, "请输入验证码".encode("utf-8"), False)
        self.assertEqual(decision.kind, "challenge_required")
        self.assertEqual(decision.action, "halt")

    def test_challenge_is_not_retried_even_with_backoff_attached(self):
        retry = RetryBackoff(base_s=2.0, jitter=fixed_jitter(0.5))
        classifier = FailureClassifier({}, retry=retry)
        decision = classifier.classify(200, "请输入验证码".encode("utf-8"), False)
        self.assertEqual(decision.action, "halt")
        # The ladder must not have been consumed: nothing was retried.
        self.assertEqual(retry.attempts, 0)

    def test_custom_markers_override_defaults(self):
        classifier = FailureClassifier(
            {"business": {"challenge_markers": ["自定义校验"]}})
        self.assertEqual(
            classifier.classify(200, "自定义校验".encode("utf-8"), False).kind,
            "challenge_required")
        # The default marker is no longer present, so it falls through to pending.
        self.assertEqual(
            classifier.classify(200, "请输入验证码".encode("utf-8"), False).kind,
            "pending")

    def test_default_marker_set_is_non_empty(self):
        self.assertGreater(len(DEFAULT_CHALLENGE_MARKERS), 10)


class BackoffTests(unittest.TestCase):
    def test_ladder_doubles_and_caps(self):
        retry = RetryBackoff(base_s=2.0, cap_s=16.0, max_attempts=4,
                             jitter=fixed_jitter(0.5))
        delays = [retry.next_delay() for _ in range(4)]
        self.assertEqual(delays, [2.0, 4.0, 8.0, 16.0])
        self.assertIsNone(retry.next_delay())

    def test_jitter_never_lets_a_429_replay_immediately(self):
        retry = RetryBackoff(base_s=4.0, jitter_ratio=0.25, jitter=fixed_jitter(0.0))
        self.assertGreaterEqual(retry.next_delay(), 2.0)

    def test_reset_restarts_the_ladder(self):
        retry = RetryBackoff(base_s=2.0, jitter=fixed_jitter(0.5))
        retry.next_delay()
        retry.next_delay()
        retry.reset()
        self.assertEqual(retry.attempts, 0)
        self.assertEqual(retry.next_delay(), 2.0)

    def test_invalid_parameters_are_rejected(self):
        for kwargs in ({"base_s": 0}, {"base_s": -1}, {"cap_s": 0},
                       {"base_s": 100, "cap_s": 10}, {"factor": 0.5},
                       {"factor": 9}, {"jitter_ratio": -0.1},
                       {"jitter_ratio": 1.5}, {"max_attempts": 0},
                       {"max_attempts": 21}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    RetryBackoff(**kwargs)

    def test_429_carries_a_delay(self):
        retry = RetryBackoff(base_s=3.0, jitter=fixed_jitter(0.5))
        classifier = FailureClassifier({}, retry=retry)
        decision = classifier.classify(429, b"", False)
        self.assertEqual(decision.action, "cooldown")
        self.assertGreater(decision.delay_s, 0.0)

    def test_5xx_carries_a_growing_delay(self):
        retry = RetryBackoff(base_s=2.0, jitter=fixed_jitter(0.5))
        classifier = FailureClassifier({}, retry=retry)
        first = classifier.classify(503, b"", False)
        second = classifier.classify(503, b"", False)
        self.assertEqual(first.action, "backoff")
        self.assertGreater(second.delay_s, first.delay_s)

    def test_without_retry_delay_is_zero(self):
        classifier = FailureClassifier({})
        self.assertEqual(classifier.classify(429, b"", False).delay_s, 0.0)

    def test_confirmed_read_resets_the_ladder(self):
        retry = RetryBackoff(base_s=2.0, jitter=fixed_jitter(0.5))
        classifier = FailureClassifier({}, retry=retry)
        classifier.classify(503, b"", False)
        self.assertEqual(retry.attempts, 1)
        classifier.note_success()
        self.assertEqual(retry.attempts, 0)

    def test_unknown_outcome_never_gets_a_retry_delay(self):
        retry = RetryBackoff(base_s=2.0, jitter=fixed_jitter(0.5))
        classifier = FailureClassifier({}, retry=retry)
        decision = classifier.classify(0, b"", False, network_error="timeout")
        self.assertEqual(decision.action, "halt")
        self.assertEqual(decision.delay_s, 0.0)
        self.assertEqual(retry.attempts, 0)

    def test_report_is_json_safe(self):
        retry = RetryBackoff(base_s=2.0, jitter=fixed_jitter(0.5))
        retry.next_delay()
        payload = retry.report()
        self.assertEqual(set(payload), {"attempts", "last_delay_s",
                                        "max_attempts", "cap_s"})


if __name__ == "__main__":
    unittest.main()
