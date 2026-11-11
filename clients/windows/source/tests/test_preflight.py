"""Verify that readiness checks do not leak secrets or fetch school content."""
import unittest
from copy import deepcopy
from core.preflight import inspect_readiness
from core.desktop_settings import load_config
from windows_gui import bundled_config


class PreflightTest(unittest.TestCase):
    def test_initial_template_is_not_ready(self):
        checks = inspect_readiness(load_config(bundled_config()), env={})
        self.assertFalse(next(x for x in checks if x.label == '真实只读协议').passed)
        self.assertFalse(next(x for x in checks if x.label == '本人登录会话').passed)
        self.assertTrue(next(x for x in checks if x.label == '安全运行模式').passed)

    def test_secrets_are_not_printed(self):
        config = deepcopy(load_config(bundled_config()))
        config['auth']['cookie'] = 'SECRET_LONG_COOKIE'
        checks = inspect_readiness(config, env={'PUSHPLUS_TOKEN': 'PRIVATE_PUSH_TOKEN'})
        display = '\n'.join(x.detail for x in checks)
        self.assertNotIn('SECRET_LONG_COOKIE', display)
        self.assertNotIn('PRIVATE_PUSH_TOKEN', display)
        self.assertFalse(next(x for x in checks if x.label == '本机配置不含会话敏感值').passed)


if __name__ == '__main__':
    unittest.main()
