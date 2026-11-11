"""V4.9 startup gates and lossless switch-backup regression tests. Offline only."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, Mock
from types import SimpleNamespace

from core.conflicts import find_conflicts
from core.desktop_settings import load_config
from core.plan_store import list_plans, load_plan, save_switch_backup
from core.preflight import format_checks, inspect_readiness, split_checks
from windows_gui import Application, bundled_config


class ReadinessV49Test(unittest.TestCase):
    def setUp(self):
        self.config = deepcopy(load_config(bundled_config()))

    def _ready_config(self):
        conf = deepcopy(self.config)
        conf['courses'] = [
            {'key': 'A', 'course_id': 'C100', 'time_slots': '1:1-2', 'group': ''},
            {'key': 'B', 'course_id': 'C200', 'time_slots': '1:2-3', 'group': ''},
        ]
        conf['protocol'].update({
            'verified': True,
            'identity_path': '/verified-identity',
            'identity_referer': '/verified-ref',
            'identity_selector': '#account',
            'entry': '/verified-entry',
            'entry_referer': '/verified-entry-ref',
            'list': '/verified-list',
            'term_field': 'term',
            'columns': {'course_id': 1},
            'table_marker': 'verified-table',
        })
        return conf

    def test_template_is_blocked_even_if_cookie_present(self):
        blockers, warnings = split_checks(inspect_readiness(self.config, env={'KINGO_COOKIE': 'very-secret'}))
        self.assertIn('目标课程', {c.label for c in blockers})
        self.assertIn('真实只读协议', {c.label for c in blockers})
        self.assertNotIn('very-secret', format_checks(inspect_readiness(self.config, env={'KINGO_COOKIE': 'very-secret'})))

    def test_manual_time_overlap_is_advisory_not_hard_block(self):
        checks = inspect_readiness(self._ready_config(), env={'KINGO_COOKIE': 'secret'})
        blockers, warnings = split_checks(checks)
        self.assertEqual(blockers, [])
        self.assertIn('手动节次冲突', {c.label for c in warnings})
        self.assertIn('A/B', format_checks(checks))

    def test_same_alternative_group_has_no_conflict(self):
        config = self._ready_config()
        for course in config['courses']:
            course['group'] = 'only-one'
        _, warnings = split_checks(inspect_readiness(config, env={'KINGO_COOKIE': 'secret'}))
        self.assertNotIn('手动节次冲突', {c.label for c in warnings})

    def test_ambiguous_matching_and_unknown_times_warn(self):
        config = self._ready_config()
        config['courses'] = [{'key': 'A', 'course_name': 'TEST', 'time_slots': ''}]
        blockers, warnings = split_checks(inspect_readiness(config, env={'KINGO_COOKIE': 'secret'}))
        self.assertFalse(blockers)
        self.assertTrue({'课程精确匹配', '节次信息完整度'}.issubset({c.label for c in warnings}))

    def test_auth_config_is_not_leaked_in_report(self):
        config = self._ready_config()
        config['auth']['cookie'] = 'ultra-secret-session'
        checks = inspect_readiness(config, env={})
        text = format_checks(checks)
        self.assertNotIn('ultra-secret-session', text)
        self.assertIn('本机配置不含会话敏感值', text)

    def test_invalid_time_string_is_blocker(self):
        config = self._ready_config()
        config['courses'][0]['time_slots'] = 'Monday, 8am'
        blockers, _ = split_checks(inspect_readiness(config, env={'KINGO_COOKIE': 'secret'}))
        self.assertIn('手动节次格式', {c.label for c in blockers})

    def test_empty_course_list_is_not_ready(self):
        config = self._ready_config()
        config['courses'] = []
        blockers, _ = split_checks(inspect_readiness(config, env={'KINGO_COOKIE': 'secret'}))
        self.assertIn('目标课程', {c.label for c in blockers})

    def test_backups_are_independent_and_have_no_secrets(self):
        config = self._ready_config()
        config['auth']['cookie'] = 'not-for-backup'
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'plans'
            first = save_switch_backup(folder, config)
            config['courses'][0]['course_id'] = 'OTHER'
            second = save_switch_backup(folder, config)
            self.assertNotEqual(first, second)
            self.assertEqual(len(list_plans(folder)), 2)
            recovered = load_plan(folder, first, self.config)
            self.assertEqual(recovered['courses'][0]['course_id'], 'C100')
            files = '\n'.join(p.read_text(encoding='utf-8') for p in folder.glob('*.json'))
            self.assertNotIn('not-for-backup', files)

    def test_gui_does_not_connect_on_template_start(self):
        # Exercise the handler without a Tk display, including on headless CI.
        status = Mock()
        app = SimpleNamespace(worker=None,
                              path=SimpleNamespace(get=lambda: str(bundled_config())),
                              status=status, log=Mock())
        with patch('windows_gui.messagebox.showwarning') as warning, \
             patch('windows_gui.MonitorWorker', side_effect=AssertionError('network worker created')):
            Application.start_monitor(app)
        warning.assert_called_once()
        self.assertIsNone(app.worker)
        status.set.assert_called_once()
        self.assertIn('无法启动', status.set.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
