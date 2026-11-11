"""Offline timing reference must not make network requests or loosen live limits."""
import unittest
from unittest.mock import patch

from core.offline_benchmark import (
    REFERENCE_INTERVAL_SECONDS, REFERENCE_WORKERS,
    format_benchmark, run_offline_benchmark,
)


class OfflineBenchmarkTests(unittest.IsolatedAsyncioTestCase):
    async def test_reference_uses_two_workers_and_never_network(self):
        self.assertEqual((REFERENCE_INTERVAL_SECONDS, REFERENCE_WORKERS), (1.5, 2))
        with patch('httpx.AsyncClient', side_effect=AssertionError('network forbidden')):
            result = await run_offline_benchmark(
                interval_seconds=0.025, cycles_per_worker=3,
                simulated_processing_ms=40,
            )
        self.assertEqual(result.started, 6)
        self.assertEqual(result.finished, 6)
        self.assertEqual(result.max_concurrent, 2)
        self.assertGreater(result.mean_interval_ms, 0)
        self.assertEqual(result.reference_dispatches_per_second, 80)
        caption = format_benchmark(result)
        self.assertIn('不是实测抢课速度', caption)
        self.assertIn('没有发起 HTTP 请求', caption)
        self.assertIn('60–3600', caption)

    async def test_invalid_inputs_do_not_schedule(self):
        bad_args = (
            {'interval_seconds': 0}, {'interval_seconds': float('nan')},
            {'interval_seconds': True}, {'workers': 0}, {'workers': True},
            {'cycles_per_worker': 1}, {'simulated_processing_ms': -1},
        )
        for params in bad_args:
            with self.subTest(params=params), self.assertRaises(ValueError):
                await run_offline_benchmark(**params)

    async def test_single_worker_cannot_overlap(self):
        result = await run_offline_benchmark(interval_seconds=0.01,
            workers=1, cycles_per_worker=3, simulated_processing_ms=25)
        self.assertEqual(result.max_concurrent, 1)
        self.assertEqual(result.started, 3)


if __name__ == '__main__':
    unittest.main()
