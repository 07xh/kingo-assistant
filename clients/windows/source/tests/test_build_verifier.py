"""Offline-only tests of native Windows verification and stale-marker cleanup."""
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from tools.verify_windows_build import BuildVerificationError, verify, main


class TestBuildVerifier(unittest.TestCase):
    def test_rejects_non_windows_runners_even_with_pe_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "Fake.exe"
            exe.write_bytes(b"MZ" + b"placeholder")
            with self.assertRaisesRegex(BuildVerificationError, "Windows"):
                verify(exe, system="Linux")

    def test_rejects_invalid_pe_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "Fake.exe"
            exe.write_bytes(b"ELF")
            with self.assertRaisesRegex(BuildVerificationError, "PE"):
                verify(exe, system="Windows")

    def test_runs_both_offline_checks_and_creates_verifiable_hash(self):
        from subprocess import CompletedProcess
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "Fake.exe"
            exe.write_bytes(b"MZ" + b"harmless-test-data")
            with patch("tools.verify_windows_build.subprocess.run", side_effect=[
                CompletedProcess([], 0), CompletedProcess([], 0)]) as run:
                report = verify(exe, system="Windows")
            self.assertEqual(run.call_count, 2)
            self.assertIn("native_tk_gui_smoke", report["checks"])
            self.assertFalse(report["school_api_verified"])
            self.assertEqual(len(report["sha256"]), 64)

    def test_failure_removes_old_success_markers(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "SHA256.txt").write_text("previous")
            (folder / "build-verification.json").write_text("previous")
            with patch("sys.argv", ["verify_windows_build.py", str(folder / "missing.exe")]):
                with patch("tools.verify_windows_build.verify", side_effect=BuildVerificationError("failed")):
                    self.assertEqual(main(), 1)
            self.assertFalse((folder / "SHA256.txt").exists())
            self.assertFalse((folder / "build-verification.json").exists())


if __name__ == "__main__":
    unittest.main()
