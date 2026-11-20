"""Config and read-only monitor extraction is compatible with old GUI APIs."""
import asyncio
from pathlib import Path
import queue
import tempfile
from unittest import TestCase
from unittest.mock import AsyncMock, Mock, patch

import windows_gui
import windows_config_runtime
import windows_monitor_runtime
from windows_gui import MonitorEvent, MonitorWorker


class ConfigExtractionTests(TestCase):
    def test_gui_still_exports_original_config_functions(self):
        self.assertIs(windows_gui.app_dir, windows_config_runtime.app_dir)
        self.assertIs(windows_gui.bundled_config, windows_config_runtime.bundled_config)
        self.assertIs(windows_gui.bundled_demo, windows_config_runtime.bundled_demo)

    def test_existing_config_is_untouched_and_missing_template_not_needed(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            existing = directory / "config.yaml"
            existing.write_text("owner: preserved", encoding="utf-8")
            output = windows_config_runtime.ensure_user_config(
                directory, directory / "nonexistent-template.yaml")
            self.assertEqual(output, existing)
            self.assertEqual(output.read_text(encoding="utf-8"),
                             "owner: preserved")

    def test_missing_template_does_not_create_blank_config(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with self.assertRaises(FileNotFoundError):
                windows_config_runtime.ensure_user_config(
                    directory, directory / "missing.yaml")
            self.assertFalse((directory / "config.yaml").exists())

    def test_offline_load_does_not_resolve_runtime_state(self):
        loaded = {"courses": [], "strategy": {"dry_run": True}}
        validator = Mock()
        with patch("windows_config_runtime.load_config",
                   return_value=loaded) as load, \
             patch("windows_config_runtime.resolve_state_file",
                   side_effect=AssertionError("unexpected runtime state")):
            result = windows_config_runtime.load_validated_config(
                Path("owner.yaml"), offline=True, validator=validator)
        self.assertIs(result, loaded)
        load.assert_called_once_with(Path("owner.yaml"))
        validator.assert_called_once_with(loaded, check_only=True)

    def test_live_load_resolves_local_path_then_validates(self):
        original = {"user": "example"}
        resolved = {"user": "example", "runtime": True}
        validator = Mock()
        with patch("windows_config_runtime.load_config",
                   return_value=original), \
             patch("windows_config_runtime.resolve_state_file",
                   return_value=resolved) as resolver:
            value = windows_config_runtime.load_validated_config(
                Path("owner.yaml"), offline=False,
                user_directory=Path("LOCAL-APP-FILES"),
                validator=validator,
            )
        self.assertIs(value, resolved)
        resolver.assert_called_once_with(
            original, Path("LOCAL-APP-FILES"))
        validator.assert_called_once_with(resolved, check_only=False)

    def test_live_config_without_directory_fails_closed(self):
        with patch("windows_config_runtime.load_config", return_value={}), \
             patch("windows_config_runtime.resolve_state_file") as resolver:
            with self.assertRaises(ValueError):
                windows_config_runtime.load_validated_config(
                    Path("config.yaml"), validator=Mock())
        resolver.assert_not_called()

    def test_gui_ensure_config_uses_patchable_original_app_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            with patch("windows_gui.app_dir", return_value=base):
                result = windows_gui.ensure_config()
            self.assertEqual(result, base / "config.yaml")
            self.assertTrue(result.is_file())


class MonitorExtractionTests(TestCase):
    def test_original_gui_names_reexport_monitor_worker_and_event(self):
        self.assertIs(MonitorWorker, windows_monitor_runtime.MonitorWorker)
        self.assertIs(MonitorEvent, windows_monitor_runtime.MonitorEvent)

    def test_worker_exception_never_emits_raw_school_details(self):
        updates = queue.Queue()
        monitor = MonitorWorker({}, updates)
        with patch.object(
            MonitorWorker, "_run", new_callable=AsyncMock,
            side_effect=RuntimeError("PRIVATE_COOKIE_AND_STUDENT_ID"),
        ):
            monitor._thread_main()
        events = [updates.get_nowait(), updates.get_nowait()]
        self.assertEqual(events[0].kind, "error")
        self.assertEqual(events[0].value, "RuntimeError")
        self.assertEqual(events[1].value, "failed")
        self.assertNotIn("PRIVATE_COOKIE_AND_STUDENT_ID", repr(events))

    def test_worker_without_exe_session_never_initializes_school_client(self):
        monitor = MonitorWorker(
            {"auth": {"cookie": "LEGACY_COOKIE"}}, queue.Queue())
        with patch.dict("os.environ", {"KINGO_COOKIE": "ENV_COOKIE"}), \
             patch("windows_monitor_runtime.KingoClient",
                   side_effect=AssertionError("unexpected school HTTP")):
            with self.assertRaisesRegex(RuntimeError, "EXE"):
                asyncio.run(monitor._run())
        self.assertIsNone(monitor.client)

    def test_stopping_before_run_never_initializes_school_client(self):
        monitor = MonitorWorker({}, queue.Queue())
        monitor._stop_requested.set()
        with patch("windows_monitor_runtime.KingoClient",
                   side_effect=AssertionError("unexpected school HTTP")):
            monitor._thread_main()
        event = monitor.updates.get_nowait()
        self.assertEqual(event.kind, "finished")
        self.assertEqual(event.value, "stopped")


if __name__ == "__main__":
    import unittest
    unittest.main()
