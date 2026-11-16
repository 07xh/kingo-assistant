"""End-to-end local JSONL smoke checks for the long-lived Lite sidecar.

These tests never send a school request, log in, or retain credentials.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest import TestCase


SCRIPT = Path(__file__).resolve().parents[1] / "lite_bridge.py"


class LiteIpcTests(TestCase):
    def run_sidecar(self, commands: list[dict], *, final_newline: bool = True):
        with tempfile.TemporaryDirectory() as directory:
            env = dict(os.environ)
            env["LOCALAPPDATA"] = directory
            env["APPDATA"] = directory
            # On non-Windows, app_dir() uses home; confine it to the test.
            env["HOME"] = directory
            lines = "\n".join(json.dumps(item) for item in commands)
            if final_newline:
                lines += "\n"
            completed = subprocess.run(
                [sys.executable, str(SCRIPT), "--serve"],
                input=lines, text=True, encoding="utf-8",
                capture_output=True, cwd=str(SCRIPT.parent), env=env,
                timeout=40, check=False,
            )
            return completed

    def test_three_requests_share_process_without_online_calls(self):
        commands = [
            {"action": "session", "payload": None},
            {"action": "restore-readiness", "payload": None},
            {"action": "monitor-status", "payload": None},
        ]
        result = self.run_sidecar(commands)
        # This test contains no personal tokens, so safe diagnostics may
        # include its synthetic sidecar response if transport framing fails.
        self.assertEqual(result.returncode, 0, "stdout=" + result.stdout[:3000]
                         + " stderr=" + result.stderr[:3000])
        lines = [json.loads(row) for row in result.stdout.splitlines()]
        self.assertEqual(len(lines), 3)
        self.assertTrue(all(row["ok"] is True for row in lines))
        self.assertEqual(lines[0]["data"]["status"], "not_logged_in")
        self.assertFalse(lines[0]["data"]["saved_session_available"])
        self.assertEqual(lines[1]["data"]["code"], "no_saved_session")
        self.assertFalse(lines[1]["data"]["may_attempt"])
        self.assertFalse(lines[1]["data"]["school_verified"])
        self.assertEqual(lines[2]["data"]["phase"], "idle")
        self.assertFalse(lines[2]["data"]["active"])

    def test_secret_is_never_reflected_from_malformed_login_request(self):
        password = "never-echo-this-credential"
        command = {
            "action": "login",
            "payload": {"username": "student01", "password": password, "remember": "yes"},
        }
        result = self.run_sidecar([command])
        self.assertEqual(result.returncode, 0)
        body = json.loads(result.stdout.strip())
        self.assertFalse(body["ok"])
        self.assertEqual(body["error"], "SESSION_REQUEST_INVALID")
        self.assertNotIn(password, result.stdout)
        self.assertNotIn(password, result.stderr)

    def test_unframed_message_is_rejected(self):
        command = {"action": "session", "payload": None}
        result = self.run_sidecar([command], final_newline=False)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout.strip(), "")
