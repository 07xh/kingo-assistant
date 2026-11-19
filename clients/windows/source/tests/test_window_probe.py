"""Offline tests for selection-window boundary estimation.

Pure arithmetic and validation: no clock source, no HTTP client, no school.
"""
import unittest

from core.window_probe import (
    DEFAULT_MIN_INTERVAL_MS,
    MAX_SCHEDULE_LENGTH,
    MIN_MEANINGFUL_GAP_MS,
    ProbeError,
    ProbeSchedule,
    WindowPhase,
    WindowProbe,
    estimate_flip,
    replay,
)

T0 = 1_766_275_200.0     # 2026-12-21 00:00:00 UTC, arbitrary but fixed


def probe(offset_s: float, phase: WindowPhase | str, source: str = "prohibit_marker"):
    return WindowProbe(at_local=T0 + offset_s,
                       phase=phase if isinstance(phase, WindowPhase) else WindowPhase(phase),
                       source=source)


class ProbeModelTests(unittest.TestCase):
    def test_non_numeric_time_rejected(self):
        with self.assertRaises(ValueError):
            WindowProbe(at_local="now", phase=WindowPhase.OPEN)

    def test_out_of_range_time_rejected(self):
        with self.assertRaises(ValueError):
            WindowProbe(at_local=0, phase=WindowPhase.OPEN)
        with self.assertRaises(ValueError):
            WindowProbe(at_local=5e9, phase=WindowPhase.OPEN)

    def test_bad_phase_and_source_rejected(self):
        with self.assertRaises(ValueError):
            WindowProbe(at_local=T0, phase="maybe")
        with self.assertRaises(ValueError):
            WindowProbe(at_local=T0, phase=WindowPhase.OPEN, source="x" * 61)


class EstimateTests(unittest.TestCase):
    def test_boundary_is_bracketed_by_the_probe_gap(self):
        probes = [probe(-0.2, "closed"), probe(0.0, "open"), probe(0.2, "open")]
        estimate = estimate_flip(probes)
        self.assertAlmostEqual(estimate.uncertainty_ms, 200.0, places=3)
        self.assertAlmostEqual(estimate.last_closed_local, T0 - 0.2, places=3)
        self.assertAlmostEqual(estimate.observed_flip_local, T0, places=3)
        self.assertEqual(estimate.probes_used, 3)
        self.assertFalse(estimate.reopened)

    def test_denser_probing_reduces_uncertainty(self):
        coarse = estimate_flip([probe(-3.0, "closed"), probe(0.0, "open")])
        fine = estimate_flip([probe(-0.1, "closed"), probe(0.0, "open")])
        self.assertGreater(coarse.uncertainty_ms, fine.uncertainty_ms)
        self.assertLess(fine.uncertainty_ms, 200.0)

    def test_already_open_on_first_probe_is_an_upper_bound_only(self):
        estimate = estimate_flip([probe(0.0, "open"), probe(0.5, "open")])
        self.assertIsNone(estimate.last_closed_local)
        self.assertEqual(estimate.uncertainty_ms, 0.0)
        self.assertIn("上界", estimate.detail)

    def test_all_closed_means_not_flipped_yet(self):
        with self.assertRaises(ProbeError) as caught:
            estimate_flip([probe(-1.0, "closed"), probe(0.0, "closed")])
        self.assertIn("尚未翻转", str(caught.exception))

    def test_empty_history_is_an_error(self):
        with self.assertRaises(ProbeError):
            estimate_flip([])

    def test_duplicate_timestamps_are_rejected(self):
        with self.assertRaises(ProbeError):
            estimate_flip([probe(0.0, "closed"), probe(0.0, "open")])

    def test_out_of_order_input_is_sorted_not_rejected(self):
        estimate = estimate_flip([probe(0.2, "open"), probe(-0.2, "closed")])
        self.assertAlmostEqual(estimate.uncertainty_ms, 400.0, places=3)

    def test_unknown_probes_cannot_anchor_either_side(self):
        probes = [probe(-1.0, "closed"), probe(-0.5, "unknown"),
                  probe(0.0, "open")]
        estimate = estimate_flip(probes)
        # The UNKNOWN sample must not shorten the CLOSED->OPEN bracket.
        self.assertAlmostEqual(estimate.uncertainty_ms, 1000.0, places=3)

    def test_reopen_is_flagged_and_first_transition_used(self):
        probes = [probe(-1.0, "closed"), probe(0.0, "open"),
                  probe(1.0, "closed"), probe(2.0, "open")]
        estimate = estimate_flip(probes)
        self.assertTrue(estimate.reopened)
        self.assertAlmostEqual(estimate.observed_flip_local, T0, places=3)

    def test_clock_offset_yields_server_side_instant(self):
        probes = [probe(-0.2, "closed"), probe(0.0, "open")]
        estimate = estimate_flip(probes, clock_offset_ms=1250.0)
        self.assertAlmostEqual(estimate.server_flip_epoch, T0 + 1.25, places=3)

    def test_without_offset_server_instant_is_absent_not_guessed(self):
        estimate = estimate_flip([probe(-0.2, "closed"), probe(0.0, "open")])
        self.assertIsNone(estimate.server_flip_epoch)

    def test_network_half_rtt_widens_the_band(self):
        probes = [probe(-0.2, "closed"), probe(0.0, "open")]
        plain = estimate_flip(probes)
        widened = estimate_flip(probes, network_half_rtt_ms=90.0)
        self.assertAlmostEqual(widened.uncertainty_ms, plain.uncertainty_ms + 90.0,
                               places=3)

    def test_lead_is_bounded_and_non_negative(self):
        for gap in (-0.05, 0.0, 0.2, 3.0):
            probes = [probe(gap - 0.2, "closed"), probe(gap, "open")]
            estimate = estimate_flip(probes, network_half_rtt_ms=40.0)
            self.assertGreaterEqual(estimate.recommended_lead_ms, 0.0)
            self.assertLessEqual(estimate.recommended_lead_ms,
                                 estimate.uncertainty_ms)

    def test_negative_rtt_and_bad_offset_rejected(self):
        probes = [probe(-0.2, "closed"), probe(0.0, "open")]
        with self.assertRaises(ValueError):
            estimate_flip(probes, network_half_rtt_ms=-1.0)
        with self.assertRaises(ValueError):
            estimate_flip(probes, clock_offset_ms="soon")

    def test_mapping_is_json_safe(self):
        import json
        mapping = estimate_flip([probe(-0.2, "closed"), probe(0.0, "open")]
                                ).as_dict()
        self.assertIsInstance(json.dumps(mapping), str)
        self.assertIn("uncertainty_ms", mapping)


class ScheduleTests(unittest.TestCase):
    def test_default_interval_is_rejected_because_floor_is_thirty_seconds(self):
        # The whole point: a 200 ms burst is not granted by default.
        with self.assertRaises(ValueError) as caught:
            ProbeSchedule(target_epoch=T0, interval_ms=200.0)
        self.assertIn("低于已声明的许可下限", str(caught.exception))

    def test_explicit_lower_floor_allows_a_burst(self):
        schedule = ProbeSchedule(target_epoch=T0, interval_ms=200.0,
                                 min_interval_ms=200.0, lead_ms=1000.0,
                                 tail_ms=1000.0)
        offsets = schedule.offsets_ms()
        self.assertEqual(offsets[0], -1000.0)
        self.assertEqual(offsets[-1], 1000.0)
        self.assertEqual(len(offsets), 11)
        self.assertEqual(schedule.timestamps()[0], T0 - 1.0)

    def test_default_schedule_respects_the_floor(self):
        schedule = ProbeSchedule(target_epoch=T0, interval_ms=30_000.0)
        self.assertEqual(schedule.min_interval_ms, DEFAULT_MIN_INTERVAL_MS)
        self.assertEqual(len(schedule.offsets_ms()), 1)

    def test_oversized_schedule_is_refused(self):
        with self.assertRaises(ValueError) as caught:
            ProbeSchedule(target_epoch=T0, interval_ms=MIN_MEANINGFUL_GAP_MS,
                          min_interval_ms=MIN_MEANINGFUL_GAP_MS,
                          lead_ms=60_000.0, tail_ms=60_000.0)
        self.assertIn(str(MAX_SCHEDULE_LENGTH), str(caught.exception))

    def test_invalid_fields_rejected(self):
        with self.assertRaises(ValueError):
            ProbeSchedule(target_epoch=T0, interval_ms=-1.0, min_interval_ms=1.0)
        with self.assertRaises(ValueError):
            ProbeSchedule(target_epoch=T0, interval_ms=1000.0, min_interval_ms=1000.0,
                          lead_ms=-5.0)

    def test_report_exposes_the_declared_floor(self):
        report = ProbeSchedule(target_epoch=T0, interval_ms=200.0,
                               min_interval_ms=200.0).report()
        self.assertEqual(report["min_interval_ms"], 200.0)
        self.assertIn("probes", report)


class ReplayTests(unittest.TestCase):
    def test_pairs_convert(self):
        built = replay([(T0 - 0.2, "closed"), (T0, "open")])
        self.assertEqual([p.phase for p in built],
                         [WindowPhase.CLOSED, WindowPhase.OPEN])

    def test_unknown_phase_name_is_an_error(self):
        with self.assertRaises(ProbeError):
            replay([(T0, "half-open")])


if __name__ == "__main__":
    unittest.main()
