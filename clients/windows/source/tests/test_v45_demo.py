"""V4.5 fictional demo behavior and fail-closed Windows packaging tests."""
from __future__ import annotations

import asyncio
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

from core.client import AuthenticationError, ProtocolError, RateLimitError, Status
from core.demo import run_demo
from core.offline_selftest import run_selftest
from core.scheduler import Scheduler
from windows_gui import bundled_config, bundled_demo


class FictionalDemoTest(unittest.TestCase):
    def test_three_snapshots_and_available_transition(self):
        report = run_demo(bundled_demo())
        self.assertEqual(len(report.steps), 3)
        self.assertEqual(len(report.courses), 2)
        self.assertEqual(report.steps[0].newly_available, ())
        self.assertEqual(report.steps[1].newly_available, ("target-B",))
        self.assertEqual(report.steps[2].newly_available, ())
        self.assertTrue(report.steps[1].states["target-B"].startswith("虚构样例：发现余量"))
        self.assertEqual(report.steps[2].states["target-B"], "虚构样例：已满")

    def test_demo_does_not_open_school_network(self):
        with patch('core.client.httpx.AsyncClient', side_effect=AssertionError('network forbidden')):
            run_demo(bundled_demo())
            run_selftest(bundled_config(), bundled_demo())

    def test_missing_demo_snapshot_fails(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'offline-config.yaml').write_bytes((bundled_demo() / 'offline-config.yaml').read_bytes())
            with self.assertRaises((FileNotFoundError, OSError, ValueError)):
                run_demo(root)

    def test_no_real_protocol_in_demo(self):
        import yaml
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            for item in bundled_demo().iterdir():
                (root / item.name).write_bytes(item.read_bytes())
            config = yaml.safe_load((root / 'offline-config.yaml').read_text(encoding='utf-8'))
            config['protocol']['verified'] = True
            (root / 'offline-config.yaml').write_text(yaml.safe_dump(config), encoding='utf-8')
            with self.assertRaises((ValueError, AssertionError)):
                run_demo(root)


class ReadOnlyFailureCategoryTest(unittest.IsolatedAsyncioTestCase):
    async def _exercise(self, error):
        stop = asyncio.Event()
        client = SimpleNamespace(stop=stop, fetch_course_list=AsyncMock(side_effect=error))
        config = {
            'strategy': {'mode': 'monitor', 'max_cycles': 1, 'interval_range': [60, 60]},
            'courses': [{'key': 'LOCAL_TEST', 'course_id': 'X1'}],
            'xnxq': 'DEMO', 'notify': {'enabled': False},
        }
        scheduler = Scheduler(client, None, config)
        await scheduler.run()
        self.assertTrue(stop.is_set())
        return scheduler

    async def test_login_expired_shows_clear_state(self):
        s = await self._exercise(AuthenticationError('session invalid'))
        self.assertEqual(s.states['LOCAL_TEST'], Status.AUTH_EXPIRED.value)
        self.assertIn('重新登录', s.last_error)

    async def test_rate_limited_never_retries(self):
        s = await self._exercise(RateLimitError('rate limited'))
        self.assertEqual(s.states['LOCAL_TEST'], Status.RATE_LIMITED.value)
        self.assertIn('已停止', s.last_error)

    async def test_protocol_mismatch_explained_safely(self):
        s = await self._exercise(ProtocolError('sensitive page data must not leak'))
        self.assertEqual(s.states['LOCAL_TEST'], Status.READ_ERROR.value)
        self.assertIn('页面结构', s.last_error)
        self.assertNotIn('sensitive page data', s.last_error)

    async def test_invalid_config_error_explained(self):
        s = await self._exercise(ValueError('should not leak untrusted user input'))
        self.assertIn('配置错误', s.last_error)
        self.assertNotIn('untrusted user input', s.last_error)


if __name__ == '__main__':
    unittest.main()
