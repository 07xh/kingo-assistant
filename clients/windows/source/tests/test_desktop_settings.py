"""V4.3 desktop controls, all tests strictly offline and headless."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from core.desktop_settings import (INTERVAL_CUSTOM, INTERVAL_PRESETS,
                                   interval_preset_for, parse_interval_range,
                                   quick_copy_value,
                                   load_config, normalize_course,
                                   resolve_state_file, save_config_atomic,
                                   scrub_secrets, status_changes, update_config)
from windows_gui import bundled_config


class DesktopSettingsTest(unittest.TestCase):
    def setUp(self):
        self.sample = load_config(bundled_config())

    def updated(self, **overrides):
        params = {
            'courses': [{'key': 'A', 'course_id': 'ENG101', 'course_name': '电路基础',
                         'class_id': 'T2', 'teacher': '张老师'}],
            'semester': '2026-2027-1', 'mode': 'monitor',
            'target_time': '2026-12-21 08:00:00',
            'interval_low': 75, 'interval_high': 100, 'max_cycles': 12,
        }
        params.update(overrides)
        return update_config(self.sample, **params)

    def test_update_courses_and_schedule_preserve_unverified_protocol(self):
        revision = self.updated()
        self.assertEqual(revision['courses'][0]['class_id'], 'T2')
        self.assertEqual(revision['strategy']['interval_range'], [75, 100])
        self.assertFalse(revision['protocol']['verified'])
        self.assertTrue(revision['strategy']['dry_run'])
        self.assertEqual(self.sample['courses'][0]['course_id'], 'FILL_FROM_OFFICIAL_PAGE')

    def test_invalid_courses_and_limits_rejected(self):
        bad_cases = (
            {'courses': [{'key':'A'}]},
            {'courses': [{'key': 'x'}]},
            {'courses': [{'key': 'x', 'course_id': 'A'}, {'key': 'x', 'course_id': 'B'}]},
            {'courses': [{'key': 'A\nB', 'course_id': 'TEST'}]},
            {'interval_low': 1},
            {'interval_low': 120, 'interval_high': 60},
            {'max_cycles': 10001},
            {'mode': 'invented'},
            {'semester': ''},
            {'target_time': 'invalid', 'mode': 'burst'},
        )
        for case in bad_cases:
            with self.subTest(case=case):
                with self.assertRaises(ValueError):
                    self.updated(**case)

    def test_interval_presets_and_custom_values(self):
        for label, pair in INTERVAL_PRESETS.items():
            with self.subTest(label=label):
                self.assertEqual(interval_preset_for(*pair), label)
                self.assertEqual(parse_interval_range(*map(str, pair)), pair)
                saved = self.updated(interval_low=pair[0], interval_high=pair[1])
                self.assertEqual(saved["strategy"]["interval_range"], list(pair))
        self.assertEqual(interval_preset_for("75", "135"), INTERVAL_CUSTOM)
        self.assertEqual(parse_interval_range(" 75 ", " 135 "), (75, 135))
        saved = self.updated(interval_low=75, interval_high=135)
        self.assertEqual(saved["strategy"]["interval_range"], [75, 135])
        self.assertEqual(parse_interval_range("3600", "3600"), (3600, 3600))

    def test_invalid_interval_inputs_rejected(self):
        bad = [("59", "60"), ("60", "3601"), ("120", "60"),
               ("", "120"), ("abc", "120"), ("60.5", "120"),
               ("-60", "60"), ("６０", "120")]
        for low, high in bad:
            with self.subTest(low=low, high=high):
                with self.assertRaisesRegex(ValueError, "查询间隔"):
                    parse_interval_range(low, high)

    def test_quick_copy_allows_only_exact_course_fields(self):
        target = {"key": "one", "course_id": "EL101", "class_id": "C2",
                  "course_name": "电路", "teacher": "李老师", "cookie": "SECRET"}
        self.assertEqual(quick_copy_value(target, "course_id"), "EL101")
        self.assertEqual(quick_copy_value(target, "class_id"), "C2")
        self.assertEqual(quick_copy_value(target, "course_name"), "电路")
        for field in ("teacher", "cookie", "__dict__"):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    quick_copy_value(target, field)

    def test_quick_copy_rejects_placeholders_and_missing_values(self):
        for target, field in (
            (None, "course_id"),
            ({"course_id": "FILL_FROM_OFFICIAL_PAGE"}, "course_id"),
            ({"course_id": "SECOND_OPTION_ID"}, "course_id"),
            ({"course_id": ""}, "course_id"),
            ({"class_id": "  "}, "class_id"),
            ({"course_name": "A\nB"}, "course_name"),
        ):
            with self.subTest(target=target, field=field):
                with self.assertRaises(ValueError):
                    quick_copy_value(target, field)

    def test_course_edit_keeps_extra_data_but_rejects_controls(self):
        result = normalize_course({'key': '1', 'course_id': 'A', 'group': 'other', 'teacher': None})
        self.assertEqual(result['group'], 'other')
        self.assertEqual(result['teacher'], '')
        with self.assertRaises(ValueError):
            normalize_course({'key': 'bad\u0000', 'course_id': 'A'})

    def test_gui_save_redacts_secrets_and_writes_atomically(self):
        config = self.updated()
        config['auth']['cookie'] = 'PRIVATE_SESSION'
        config['notify']['pushplus_token'] = 'PRIVATE_PUSH'
        config['notify']['serverchan_token'] = 'PRIVATE_SERVERCHAN'
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / 'config.yaml'
            file.write_text('old state', encoding='utf-8')
            redacted = save_config_atomic(file, config)
            saved = file.read_text(encoding='utf-8')
            self.assertEqual(set(redacted), {'auth.cookie', 'notify.pushplus_token', 'notify.serverchan_token'})
            self.assertNotIn('PRIVATE_', saved)
            self.assertNotIn('old state', saved)
            self.assertEqual(load_config(file)['courses'][0]['course_id'], 'ENG101')
            self.assertEqual(list(Path(tmp).glob('*.tmp')), [])
            self.assertEqual(config['auth']['cookie'], 'PRIVATE_SESSION')

    def test_unsafe_save_keeps_old_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / 'config.yaml'
            file.write_text('old state', encoding='utf-8')
            bad = deepcopy(self.sample)
            bad['strategy']['dry_run'] = False
            with self.assertRaises(ValueError):
                save_config_atomic(file, bad)
            self.assertEqual(file.read_text(encoding='utf-8'), 'old state')

    def test_state_file_relative_is_under_user_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = self.updated()
            user_dir = Path(tmp).resolve()
            runtime = resolve_state_file(config, user_dir)
            expected = (user_dir / 'data/notification-state.json').resolve()
            self.assertEqual(Path(runtime['strategy']['state_file']).resolve(), expected)
            self.assertEqual(config['strategy']['state_file'], 'data/notification-state.json')
            config['strategy']['state_file'] = '../outside.json'
            with self.assertRaises(ValueError):
                resolve_state_file(config, user_dir)

    def test_available_transition_only_once(self):
        before = {'A': '已满', 'B': '发现余量（2）', 'C': '未知'}
        after = {'A': '发现余量（1）', 'B': '发现余量（3）', 'C': '已满'}
        self.assertEqual(status_changes(before, after), ['A'])

    def test_reject_oversized_yaml(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / 'huge.yaml'
            file.write_text('x' * (256 * 1024 + 1), encoding='utf-8')
            with self.assertRaises(ValueError):
                load_config(file)


if __name__ == '__main__':
    unittest.main()
