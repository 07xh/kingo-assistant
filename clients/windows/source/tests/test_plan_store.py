"""Offline regression tests for user-plan persistence and class-slot hints."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
import yaml

from core.conflicts import find_conflicts, parse_slots
from core.desktop_settings import normalize_course
from core.plan_store import (delete_plan, list_plans, load_plan, save_plan)

BASE = Path(__file__).resolve().parents[1]


class TestPlanStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name) / 'plans'
        self.config = yaml.safe_load((BASE / 'config.example.yaml').read_text(encoding='utf-8'))
        self.config['auth']['cookie'] = 'VERY_PRIVATE_COOKIE'
        self.config['notify']['pushplus_token'] = 'VERY_PRIVATE_PUSH'

    def test_save_and_restore_keeps_credentials_and_protocol_only_in_current_config(self):
        config = deepcopy(self.config)
        config['courses'] = [{"key": "choice-A", "course_id": "TEST-1", "course_name": "已确认课程名",
                              "teacher": "", "group": "A", "time_slots": "1:1-2"}]
        save_plan(self.folder, '大一上/首选', config)
        content = ''.join(p.read_text(encoding='utf8') for p in self.folder.iterdir())
        self.assertNotIn('VERY_PRIVATE_COOKIE', content)
        self.assertNotIn('VERY_PRIVATE_PUSH', content)
        self.assertNotIn('protocol', content)
        self.assertEqual(list_plans(self.folder), ['大一上/首选'])
        current = deepcopy(self.config)
        current['protocol']['identity_path'] = '/verified-only-current'
        loaded = load_plan(self.folder, '大一上/首选', current)
        self.assertEqual(loaded['courses'][0]['course_id'], config['courses'][0]['course_id'])
        self.assertEqual(loaded['courses'][0]['time_slots'], '1:1-2')
        self.assertEqual(loaded['protocol'], current['protocol'])
        self.assertEqual(loaded['auth'], current['auth'])
        self.assertEqual(loaded['notify'], current['notify'])
        self.assertTrue(loaded['strategy']['dry_run'])
        delete_plan(self.folder, '大一上/首选')
        self.assertEqual(list_plans(self.folder), [])

    def test_refuses_invalid_names_and_symlink(self):
        for name in ('', '    ', 'x' * 41, 'name\nline'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                save_plan(self.folder, name, self.config)
        self.folder.mkdir(exist_ok=True)
        outside = Path(self.tmp.name) / 'secrets'
        outside.write_text('DO_NOT_OVERWRITE')
        import hashlib
        target = self.folder / (hashlib.sha256('Unsafe'.encode()).hexdigest() + '.json')
        try:
            target.symlink_to(outside)
        except (OSError, NotImplementedError):
            return
        with self.assertRaises(ValueError):
            save_plan(self.folder, 'Unsafe', self.config)
        self.assertEqual(outside.read_text(), 'DO_NOT_OVERWRITE')

    def test_will_not_restore_unverified_write_mode(self):
        save_plan(self.folder, '普通方案', self.config)
        obj = load_plan(self.folder, '普通方案', self.config)
        self.assertTrue(obj['strategy']['dry_run'])


class TestConflicts(unittest.TestCase):
    def test_parse_slots(self):
        self.assertEqual(parse_slots('1:1-2;3:5-6')[0].first, 1)
        self.assertEqual(parse_slots('1:1-2;1:1-2'), parse_slots('1:1-2'))
        for text in ('1:3-2', '0:1-2', '1:0-2', '8:1-2', '1:2-17', '1:5', '1:1-2;', '1:1-2;2:'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_slots(text)

    def test_alternative_groups_skip_each_other_but_cross_group_warns(self):
        courses = [
            {'key':'a','group':'same','time_slots':'1:1-3'},
            {'key':'b','group':'same','time_slots':'1:2-4'},
            {'key':'c','group':'else','time_slots':'1:3-5'},
            {'key':'d','time_slots':''},
        ]
        conflicts, missing = find_conflicts(courses)
        self.assertEqual({(x.first_key,x.second_key) for x in conflicts}, {('a','c'),('b','c')})
        self.assertEqual(missing, ['d'])
        self.assertEqual(conflicts[0].first_period, 3)

    def test_normalize_rejects_incorrect_schedule(self):
        with self.assertRaises(ValueError):
            normalize_course({'key':'A','course_id':'ABC','time_slots':'Monday 9am'})
