"""Passwordless continuity tests: explicit consent, OS encryption and proof gates.

Live school I/O is never performed by these tests.
"""
from __future__ import annotations

from dataclasses import dataclass
import sys
import tempfile
from pathlib import Path
from unittest import TestCase, skipUnless
from unittest.mock import AsyncMock, patch

from core.native_login import NativeLoginResult
from core.ncwu_term_probe import TermProbeResult
from lite_session import LiteSession
from lite_session_vault import WindowsSessionVault, SavedSchoolSession


@dataclass
class MemoryVault:
    saved: SavedSchoolSession | None = None
    cleared: int = 0
    def save(self, username, cookie):
        self.saved = SavedSchoolSession(username, cookie, 0.0)
    def load(self):
        return self.saved
    def clear(self):
        self.saved = None
        self.cleared += 1


class PasswordlessRestoreTests(TestCase):
    def test_opt_in_remembers_school_cookie_but_never_password(self):
        vault = MemoryVault()
        session = LiteSession(vault=vault)
        result = NativeLoginResult("verified", "JSESSIONID=private", "jwmis.ncwu.edu.cn")
        with patch("lite_session.login_ncwusigned", new_callable=AsyncMock, return_value=result):
            reply = session.login({}, "student01", "never-store-password", remember=True)
        self.assertTrue(reply["session"]["persistent"])
        self.assertTrue(reply["session"]["saved_session_available"])
        self.assertEqual(vault.saved.username, "student01")
        self.assertEqual(vault.saved.cookie, "JSESSIONID=private")
        self.assertNotIn("private", str(reply))
        self.assertNotIn("never-store-password", str(reply))

    def test_default_login_does_not_remember_any_cookie(self):
        vault = MemoryVault(SavedSchoolSession("previous", "JSESSIONID=old", 0.0))
        session = LiteSession(vault=vault)
        result = NativeLoginResult("verified", "JSESSIONID=new", "jwmis.ncwu.edu.cn")
        with patch("lite_session.login_ncwusigned", new_callable=AsyncMock, return_value=result):
            reply = session.login({}, "student01", "local-password", remember=False)
        self.assertFalse(reply["session"]["persistent"])
        self.assertFalse(reply["session"]["saved_session_available"])
        self.assertEqual(vault.cleared, 1)

    def test_app_restart_requires_explicit_independent_identity_read(self):
        vault = MemoryVault(SavedSchoolSession("student01", "JSESSIONID=sensitive", 0.0))
        session = LiteSession(vault=vault)
        self.assertEqual(session.info()["status"], "not_logged_in")
        self.assertTrue(session.info()["saved_session_available"])
        verified_config = {"known_year_term": {"verified": True, "identity_binding_verified": True}}
        with patch("lite_session.validate_term_contract", return_value=None), \
             patch("lite_session.probe_known_year_term", new_callable=AsyncMock,
                   return_value=TermProbeResult("identity_verified")) as proof:
            reply = session.restore(verified_config)
        self.assertEqual(reply["code"], "restored")
        self.assertEqual(reply["session"]["status"], "verified")
        self.assertNotIn("sensitive", str(reply))
        self.assertEqual(proof.await_count, 1)
        self.assertEqual(proof.await_args.kwargs["expected_user_code"], "student01")

    def test_unverified_identity_contract_never_sends_school_request(self):
        vault = MemoryVault(SavedSchoolSession("student01", "JSESSIONID=sensitive", 0.0))
        session = LiteSession(vault=vault)
        with patch("lite_session.probe_known_year_term", new_callable=AsyncMock) as probe:
            reply = session.restore({"known_year_term": {"verified": False}})
        self.assertEqual(reply["code"], "verification_unavailable")
        self.assertEqual(reply["session"]["status"], "not_logged_in")
        self.assertTrue(reply["session"]["saved_session_available"])
        probe.assert_not_called()

    def test_school_expiry_revokes_stored_cookie(self):
        vault = MemoryVault(SavedSchoolSession("student01", "JSESSIONID=expired", 0.0))
        session = LiteSession(vault=vault)
        with patch("lite_session.validate_term_contract", return_value=None), \
             patch("lite_session.probe_known_year_term", new_callable=AsyncMock,
                   return_value=TermProbeResult("expired")):
            reply = session.restore({"known_year_term": {"identity_binding_verified": True}})
        self.assertEqual(reply["code"], "expired")
        self.assertFalse(reply["session"]["saved_session_available"])
        self.assertEqual(vault.cleared, 1)

    def test_explicit_logout_deletes_saved_login(self):
        vault = MemoryVault(SavedSchoolSession("student01", "JSESSIONID=secret", 0.0))
        session = LiteSession("JSESSIONID=secret", "jwmis.ncwu.edu.cn", "verified",
                              vault=vault, username="student01")
        reply = session.clear(forget=True)
        self.assertFalse(reply["persistent"])
        self.assertFalse(reply["saved_session_available"])
        self.assertEqual(vault.cleared, 1)

    def test_no_automatic_school_request_on_instantiation(self):
        vault = MemoryVault(SavedSchoolSession("student01", "JSESSIONID=sensitive", 0.0))
        with patch("lite_session.probe_known_year_term", new_callable=AsyncMock) as probe:
            session = LiteSession(vault=vault)
            self.assertTrue(session.info()["saved_session_available"])
            self.assertEqual(session.info()["status"], "not_logged_in")
        probe.assert_not_called()


@skipUnless(sys.platform == "win32", "DPAPI only exists on Windows")
class WindowsDpapiVaultTests(TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.vault = WindowsSessionVault(Path(temporary.name))

    def test_real_windows_account_encryption_and_round_trip(self):
        self.vault.save("student01", "JSESSIONID=Sensitive+Value=123")
        ciphertext = self.vault.path.read_bytes()
        self.assertNotIn(b"Sensitive", ciphertext)
        saved = self.vault.load()
        self.assertIsNotNone(saved)
        self.assertEqual(saved.username, "student01")
        self.assertEqual(saved.cookie, "JSESSIONID=Sensitive+Value=123")
        self.vault.clear()
        self.assertIsNone(self.vault.load())

    def test_expired_encrypted_record_is_never_restored(self):
        with patch("lite_session_vault.time.time", return_value=1_700_000_000.0):
            self.vault.save("student01", "JSESSIONID=private")
        with patch("lite_session_vault.time.time", return_value=1_700_000_000.0 + 8*24*3600):
            self.assertIsNone(self.vault.load())

    def test_unencrypted_file_cannot_be_interpreted_as_login(self):
        self.vault.path.write_text('{"account":"student01","cookie":"JSESSIONID=secret"}')
        self.assertIsNone(self.vault.load())
