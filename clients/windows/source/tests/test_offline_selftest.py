"""Packaged self-test must stay local and fail closed."""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import yaml

from core.offline_selftest import run_selftest
from windows_gui import bundled_config, OFFICIAL_PORTAL, main


class OfflineSelfTest(unittest.TestCase):
    def test_source_template_selftest(self):
        with patch('core.client.httpx.AsyncClient', side_effect=AssertionError('network forbidden')):
            run_selftest(bundled_config())

    def test_modified_template_does_not_claim_verification(self):
        template = yaml.safe_load(bundled_config().read_text(encoding='utf-8'))
        template['protocol']['verified'] = True
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / 'invalid.yaml'
            target.write_text(yaml.safe_dump(template, allow_unicode=True), encoding='utf-8')
            with self.assertRaises((AssertionError, ValueError)):
                run_selftest(target)

    def test_invalid_cli_arguments_no_gui(self):
        with patch('windows_gui.sys.argv', ['windows_gui.py', '--unknown']):
            with patch('windows_gui.Application', side_effect=AssertionError('GUI forbidden')):
                self.assertEqual(main(), 2)

    def test_cli_selftest_no_gui(self):
        with patch('windows_gui.sys.argv', ['windows_gui.py', '--self-test']):
            with patch('windows_gui.Application', side_effect=AssertionError('GUI forbidden')):
                self.assertEqual(main(), 0)

    def test_official_site_is_school_domain(self):
        self.assertEqual(OFFICIAL_PORTAL, 'https://jwmis.ncwu.edu.cn')


if __name__ == '__main__':
    unittest.main()
