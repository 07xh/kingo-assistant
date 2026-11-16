"""Offline tests for ``Date``-rollover clock estimation.

Pure arithmetic and validation: no clock source, no HTTP client, no school.
Every sample below is synthetic, constructed from a known offset so the bracket
can be checked against the truth rather than against itself.
"""
import unittest

from core.date_flip_sync import (
    DATE_RESOLUTION_MS, MAX_SAMPLES, DateSample, DateSyncError,
    bisect_plan, estimate_offset, next_probe_after,
)

D = 1_766_275_200.0      # an arbitrary integer second
TRUE_OFFSET = 0.420      # school clock is 420 ms ahead of this machine


def sample(sent: float, received: float, server: int, age: int = 0) -> DateSample:
    return DateSample(sent_epoch=sent, received_epoch=received,
                      server_epoch=server, age_seconds=age)


def closed(**kw) -> DateSample:
    """A sample the school answered while its clock was still inside [D, D+1)."""
    sent = kw.get("sent", 0.50)
    rtt = kw.get("rtt", 0.002)
    return sample(D + sent, D + sent + rtt, int(D))


def opened(**kw) -> DateSample:
    """A sample the school answered after crossing into [D+1, D+2)."""
    sent = kw.get("sent", 0.60)
    rtt = kw.get("rtt", 0.002)
    return sample(D + sent, D + sent + rtt, int(D) + 1)


class ValidationTests(unittest.TestCase):
    def test_empty_set_rejected(self):
        with self.assertRaises(DateSyncError):
            estimate_offset([])

    def test_non_sample_rejected(self):
        with self.assertRaises(DateSyncError):
            estimate_offset([(0.0, 0.1, int(D))])

    def test_received_before_sent_rejected(self):
        with self.assertRaises(ValueError):
            sample(D + 0.5, D + 0.4, int(D))

    def test_non_integral_server_epoch_rejected(self):
        with self.assertRaises(DateSyncError):
            DateSample(sent_epoch=D + 0.5, received_epoch=D + 0.6,
                       server_epoch=D + 0.5)  # type: ignore[arg-type]

    def test_cached_date_rejected(self):
        with self.assertRaises(DateSyncError):
            sample(D + 0.5, D + 0.6, int(D), age=17)

    def test_out_of_range_epoch_rejected(self):
        with self.assertRaises(DateSyncError):
            sample(0.0, 0.1, int(D))
        with self.assertRaises(DateSyncError):
            sample(D, D + 0.1, 5_000_000_000)

    def test_duplicate_receive_instant_rejected(self):
        a = closed(sent=0.50)
        b = opened(sent=0.60, rtt=0.002)
        clash = DateSample(**{**b.__dict__, "received_epoch": a.received_epoch,
                              "sent_epoch": a.received_epoch - 0.001})
        with self.assertRaises(DateSyncError):
            estimate_offset([a, clash])

    def test_too_many_samples_rejected(self):
        with self.assertRaises(DateSyncError):
            estimate_offset([closed(sent=0.001 * i) for i in range(MAX_SAMPLES + 1)])

    def test_retrograde_date_rejected(self):
        # Later reception, smaller Date: the school clock cannot have gone
        # backwards, so at most one of these two samples is trustworthy.
        a = sample(D + 0.50, D + 0.502, int(D) + 1)
        b = sample(D + 0.60, D + 0.602, int(D))
        with self.assertRaises(DateSyncError):
            estimate_offset([a, b])


class EstimateTests(unittest.TestCase):
    def test_single_second_samples_fall_back_to_half_second(self):
        estimate = estimate_offset([closed(sent=0.10), closed(sent=0.40)])
        self.assertFalse(estimate.crossed)
        self.assertGreaterEqual(estimate.uncertainty_ms, DATE_RESOLUTION_MS / 2)
        self.assertIn("未观察到 Date 翻转", estimate.detail)

    def test_bracket_is_the_sampling_gap(self):
        estimate = estimate_offset([closed(sent=0.50, rtt=0.020), opened(sent=0.60, rtt=0.020)])
        self.assertTrue(estimate.crossed)
        # lower = max sent of Date=D = 0.500, upper = min received of Date=D+1 = 0.620
        # half-width = (0.620 - 0.500) / 2 = 60 ms
        self.assertAlmostEqual(estimate.uncertainty_ms, 60.0, delta=0.01)
        self.assertTrue(estimate.is_sub_second)

    def test_denser_sampling_tightens_the_bracket(self):
        coarse = estimate_offset([closed(sent=0.50), opened(sent=0.60)])
        fine = estimate_offset([closed(sent=0.575), opened(sent=0.583)])
        self.assertLess(fine.uncertainty_ms, coarse.uncertainty_ms)
        # lower = 0.575, upper = 0.583 + 0.002 rtt = 0.585 -> half-width 5 ms
        self.assertAlmostEqual(fine.uncertainty_ms, 5.0, delta=0.01)

    def test_true_offset_inside_the_bracket(self):
        estimate = estimate_offset([closed(sent=0.50, rtt=0.020), opened(sent=0.60, rtt=0.020)])
        low = (estimate.offset_ms - estimate.uncertainty_ms) / 1000.0
        high = (estimate.offset_ms + estimate.uncertainty_ms) / 1000.0
        self.assertLessEqual(low, TRUE_OFFSET)
        self.assertGreaterEqual(high, TRUE_OFFSET)

    def test_offset_sign_is_positive_when_school_is_ahead(self):
        estimate = estimate_offset([closed(sent=0.50), opened(sent=0.60)])
        self.assertGreater(estimate.offset_ms, 0)

    def test_offset_sign_is_negative_when_school_is_behind(self):
        # School clock 300 ms *behind*: the crossing is at local D+1+0.30
        behind_closed = sample(D + 1.20, D + 1.202, int(D))
        behind_open = sample(D + 1.35, D + 1.352, int(D) + 1)
        # Date=D at local 1.20 means server clock still inside [D, D+1)
        # => offset <= -0.20 ; Date=D+1 at 1.35 => offset >= -0.35
        estimate = estimate_offset([behind_closed, behind_open])
        self.assertLess(estimate.offset_ms, 0)

    def test_multi_second_jump_is_flagged(self):
        estimate = estimate_offset([closed(sent=0.50), sample(D + 3.5, D + 3.52, int(D) + 3)])
        self.assertEqual(estimate.span_seconds, 3)
        self.assertIn("跳过", estimate.detail)

    def test_last_closed_and_first_open_are_reported(self):
        a = closed(sent=0.50)
        b = opened(sent=0.60)
        estimate = estimate_offset([a, b])
        self.assertIs(estimate.last_closed, a)
        self.assertIs(estimate.first_open, b)

    def test_out_of_order_input_is_sorted(self):
        a = opened(sent=0.60)
        b = closed(sent=0.50)
        estimate = estimate_offset([a, b])
        self.assertTrue(estimate.crossed)
        self.assertIs(estimate.first_open, a)

    def test_self_contradictory_samples_rejected(self):
        # A "closed" sample sent after an "open" one was received.
        bad_closed = sample(D + 0.90, D + 0.902, int(D))
        bad_open = sample(D + 0.60, D + 0.602, int(D) + 1)
        with self.assertRaises(DateSyncError):
            estimate_offset([bad_open, bad_closed])


class ScheduleTests(unittest.TestCase):
    def test_no_suggestion_without_a_bracket(self):
        self.assertIsNone(next_probe_after(estimate_offset([closed(sent=0.10)])))

    def test_no_suggestion_when_target_already_met(self):
        estimate = estimate_offset([closed(sent=0.575), opened(sent=0.583)])
        self.assertIsNone(next_probe_after(estimate, target_ms=10.0))

    def test_suggestion_lands_inside_the_bracket(self):
        estimate = estimate_offset([closed(sent=0.50, rtt=0.020), opened(sent=0.60, rtt=0.020)])
        instant = next_probe_after(estimate, target_ms=10.0)
        self.assertIsNotNone(instant)
        assert instant is not None
        self.assertGreater(instant, D + 0.50)
        self.assertLess(instant, D + 0.62)

    def test_bisect_plan_is_bounded(self):
        estimate = estimate_offset([closed(sent=0.50), opened(sent=0.60)])
        plan = bisect_plan(estimate, target_ms=0.001, max_rounds=3)
        self.assertLessEqual(len(plan), 3)
        self.assertTrue(all(D + 0.50 < p < D + 0.60 for p in plan))

    def test_bisect_plan_rejects_negative_rounds(self):
        estimate = estimate_offset([closed(sent=0.50), opened(sent=0.60)])
        with self.assertRaises(DateSyncError):
            bisect_plan(estimate, max_rounds=-1)

    def test_non_positive_target_rejected(self):
        estimate = estimate_offset([closed(sent=0.50), opened(sent=0.60)])
        with self.assertRaises(DateSyncError):
            next_probe_after(estimate, target_ms=0)


if __name__ == "__main__":
    unittest.main()
