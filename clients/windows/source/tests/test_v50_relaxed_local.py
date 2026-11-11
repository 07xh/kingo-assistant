"""V5.0: relax local workflow without relaxing read-only/server protections."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from core.desktop_settings import load_config, save_config_atomic, update_config
from core.monitor_policy import validate_limits
from core.plan_store import load_plan, save_plan
from core.preflight import inspect_readiness, split_checks
from core.scheduler import Scheduler
from main import validate
from windows_gui import bundled_config


class OfflineDraftTest(unittest.TestCase):
    def setUp(self):
        self.config = deepcopy(load_config(bundled_config()))

    def test_empty_plan_is_legal_offline_but_never_online(self):
        self.config['courses'] = []
        validate(self.config, check_only=True)
        with self.assertRaises(ValueError):
            validate(self.config, check_only=False)
        blockers, _ = split_checks(inspect_readiness(self.config, env={}))
        self.assertIn('目标课程', {item.label for item in blockers})

    def test_offline_editor_can_save_restore_empty_plan(self):
        config = update_config(self.config, courses=[], semester='2026-2027-1',
                               mode='monitor', target_time='2026-12-21 08:00:00',
                               interval_low=60, interval_high=90,
                               max_cycles=0, max_duration_minutes=300)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'config.yaml'
            save_config_atomic(path, config)
            saved = load_config(path)
            self.assertEqual(saved['courses'], [])
            self.assertEqual(saved['strategy']['max_cycles'], 0)
            save_plan(root / 'plans', '空草稿', saved)
            restored = load_plan(root / 'plans', '空草稿', self.config)
            self.assertEqual(restored['courses'], [])
            self.assertEqual(restored['strategy']['max_duration_minutes'], 300)

    def test_offline_edit_does_not_depend_on_complete_live_protocol(self):
        self.config['protocol']['verified'] = True
        self.config['protocol']['list'] = ''
        validate(self.config, check_only=True)
        with self.assertRaises(ValueError):
            validate(self.config, check_only=False)

    def test_dry_run_remains_required_offline(self):
        self.config['strategy']['dry_run'] = False
        with self.assertRaises(ValueError):
            validate(self.config, check_only=True)


class FrequencyPolicyTest(unittest.TestCase):
    def setUp(self):
        self.strategy = deepcopy(load_config(bundled_config())['strategy'])

    def test_default_floor_and_count_cap(self):
        self.strategy['interval_range'] = [59, 90]
        with self.assertRaises(ValueError):
            validate_limits(self.strategy)
        self.strategy['interval_range'] = [60, 90]
        self.strategy['max_cycles'] = 10001
        with self.assertRaises(ValueError):
            validate_limits(self.strategy)

    def test_explicit_confirmed_policy_needs_source_and_valid_interval(self):
        self.strategy.update(interval_range=[45, 60], max_cycles=0,
                             max_duration_minutes=480,
                             official_frequency_policy={
                                 'manually_confirmed': True,
                                 'source': '',
                                 'min_interval_seconds': 45,
                             })
        with self.assertRaises(ValueError):
            validate_limits(self.strategy)
        self.strategy['official_frequency_policy']['source'] = '学校官方公布的只读访问规则（人工核对）'
        limits = validate_limits(self.strategy)
        self.assertEqual(limits.low, 45)
        self.assertEqual(limits.max_cycles, 0)
        self.strategy['official_frequency_policy']['min_interval_seconds'] = 1
        with self.assertRaises(ValueError):
            validate_limits(self.strategy)

    def test_unconfirmed_policy_cannot_lower_floor(self):
        self.strategy['interval_range'] = [45, 60]
        self.strategy['official_frequency_policy'] = {
            'manually_confirmed': False, 'source': 'not verified',
            'min_interval_seconds': 30,
        }
        with self.assertRaises(ValueError):
            validate_limits(self.strategy)

    def test_duration_and_nan_rejected(self):
        self.strategy['max_duration_minutes'] = 1441
        with self.assertRaises(ValueError):
            validate_limits(self.strategy)
        self.strategy['max_duration_minutes'] = 480
        self.strategy['interval_range'] = [float('nan'), 90]
        with self.assertRaises(ValueError):
            validate_limits(self.strategy)


class BoundedMonitoringTest(unittest.IsolatedAsyncioTestCase):
    async def test_zero_cycles_runs_until_stopped(self):
        c = deepcopy(load_config(bundled_config()))
        c['courses'] = [{'key': 'A', 'course_id': 'MOCK'}]
        c['strategy']['max_cycles'] = 0
        client = type('OfflineClient', (), {'stop': asyncio.Event()})()
        scheduler = Scheduler(client, None, c)

        async def offline_cycle():
            scheduler.cycles += 1
            if scheduler.cycles == 3:
                client.stop.set()

        scheduler.cycle = offline_cycle
        scheduler._sleep_or_stop = AsyncMock(return_value=True)
        await scheduler.run()
        self.assertEqual(scheduler.cycles, 3)
        self.assertTrue(client.stop.is_set())

    async def test_duration_cap_stops_even_without_cycle_cap(self):
        c = deepcopy(load_config(bundled_config()))
        c['courses'] = [{'key': 'A', 'course_id': 'MOCK'}]
        c['strategy']['max_cycles'] = 0
        c['strategy']['max_duration_minutes'] = 1
        client = type('OfflineClient', (), {'stop': asyncio.Event()})()
        watcher = Scheduler(client, None, c)
        clock = iter([0, 1, 60, 61])
        async def offline_cycle():
            watcher.cycles += 1
        watcher.cycle = offline_cycle
        watcher._sleep_or_stop = AsyncMock(return_value=True)
        with patch('core.scheduler.time.monotonic', side_effect=lambda: next(clock)):
            await watcher.run()
        self.assertEqual(watcher.cycles, 1)
        self.assertTrue(client.stop.is_set())


if __name__ == '__main__':
    unittest.main()

class TableBoundaryTest(unittest.TestCase):
    def test_other_table_cannot_supply_false_seats(self):
        from core.client import parse_courses, ProtocolError
        protocol = {'table_marker':'id="verified-catalog"', 'columns':{
            'course_id':0, 'course_name':1, 'teacher':2, 'remain':3}}
        fake = (b'<table id="verified-catalog"><tr><th>ID</th></tr></table>'
                b'<table><tr><td>TEST01</td><td>Name</td><td>Teacher</td><td>99</td></tr></table>')
        with self.assertRaises(ProtocolError):
            parse_courses(fake, protocol)

    def test_multiple_matched_tables_are_ambiguous(self):
        from core.client import parse_courses, ProtocolError
        protocol = {'table_marker':'class="matched"', 'columns':{
            'course_id':0, 'course_name':1, 'teacher':2, 'remain':3}}
        table = b'<table class="matched"><tr><td>TEST01</td><td>Name</td><td>Teacher</td><td>2</td></tr></table>'
        with self.assertRaises(ProtocolError):
            parse_courses(table + table, protocol)
