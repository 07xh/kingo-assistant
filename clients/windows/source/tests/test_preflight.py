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

    def test_native_only_readiness_rejects_config_and_env_cookies(self):
        config = deepcopy(load_config(bundled_config()))
        config["auth"]["cookie"] = "SECRET_CONFIG"
        with_env = {"KINGO_COOKIE": "SECRET_ENV"}
        report = inspect_readiness(
            config, env=with_env, native_session_only=True)
        session = next(x for x in report if x.label == "本人登录会话")
        self.assertFalse(session.passed)
        self.assertIn("EXE", session.detail)
        self.assertNotIn("SECRET_ENV", session.detail)
        self.assertNotIn("SECRET_CONFIG", session.detail)
        with_session = inspect_readiness(
            config, env=with_env, session_cookie="SID=synthetic",
            native_session_only=True)
        self.assertTrue(next(
            x for x in with_session if x.label == "本人登录会话").passed)

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
