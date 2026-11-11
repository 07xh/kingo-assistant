"""Headless checks for Windows launcher/configuration paths. No GUI and no network."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from windows_gui import app_dir, bundled_config, ensure_config, read_config


class WindowsGuiConfigTest(unittest.TestCase):
    def test_bundled_template_is_readable_offline(self):
        path = bundled_config()
        self.assertTrue(path.is_file())
        config = read_config(path, offline=True)
        self.assertEqual(config["strategy"]["dry_run"], True)
        self.assertFalse(config["protocol"]["verified"])

    def test_config_created_without_overwriting_existing(self):
        with tempfile.TemporaryDirectory() as td:
            with patch("windows_gui.app_dir", return_value=Path(td)):
                first = ensure_config()
                self.assertTrue(first.is_file())
                first.write_text("custom value", encoding="utf-8")
                self.assertEqual(ensure_config().read_text(encoding="utf-8"), "custom value")

    def test_default_real_monitor_is_forbidden(self):
        with self.assertRaises(ValueError):
            read_config(bundled_config(), offline=False)


if __name__ == "__main__":
    unittest.main()
