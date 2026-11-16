"""Passwordless continuity tests: explicit consent, OS encryption and proof gates.

Live school I/O is never performed by these tests.
"""
from __future__ import annotations

from dataclasses import dataclass
import sys
import subprocess
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

    def test_old_encrypted_token_is_not_mislabeled_as_current_session(self):
        vault = MemoryVault(SavedSchoolSession("student01", "JSESSIONID=old", 0.0))
        session = LiteSession("JSESSIONID=new", "jwmis.ncwu.edu.cn", "verified",
                              vault=vault, username="student01")
        self.assertTrue(session.info()["saved_session_available"])
        self.assertFalse(session.info()["persistent"])

    def test_failed_encrypted_update_does_not_claim_current_session_saved(self):
        class FailingVault(MemoryVault):
            def save(self, username, cookie):
                raise OSError("mock secure storage unavailable")
        vault = FailingVault(SavedSchoolSession("student01", "JSESSIONID=old", 0.0))
        session = LiteSession(vault=vault)
        result = NativeLoginResult("verified", "JSESSIONID=new", "jwmis.ncwu.edu.cn")
        with patch("lite_session.login_ncwusigned", new_callable=AsyncMock, return_value=result):
            reply = session.login({}, "student01", "secret-only-in-input", remember=True)
        self.assertTrue(reply["remember_error"])
        self.assertTrue(reply["session"]["verified_at_login"])
        self.assertFalse(reply["session"]["persistent"])
        self.assertNotIn("secret-only-in-input", str(reply))

    def test_switch_account_does_not_keep_previous_saved_cookie(self):
        vault = MemoryVault(SavedSchoolSession("other-student", "JSESSIONID=old", 0.0))
        session = LiteSession(vault=vault)
        with patch("lite_session.login_ncwusigned", new_callable=AsyncMock,
                   return_value=NativeLoginResult("login_rejected")):
            result = session.login({}, "student01", "incorrect-password", remember=True)
        self.assertEqual(result["code"], "login_rejected")
        self.assertIsNone(vault.saved)
        self.assertGreaterEqual(vault.cleared, 1)
        self.assertEqual(result["session"]["status"], "not_logged_in")
        self.assertFalse(result["session"]["saved_session_available"])

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

    def test_passwordless_readiness_is_local_only_and_truthful(self):
        vault = MemoryVault(SavedSchoolSession("student01", "SESSION_ID=sample", 0.0))
        session = LiteSession(vault=vault)
        with patch("lite_session.probe_known_year_term", new_callable=AsyncMock) as net:
            denied = session.restore_readiness({"known_year_term": {"verified": False}})
            self.assertEqual(denied["code"], "identity_unverified")
            self.assertFalse(denied["may_attempt"])
            with patch("lite_session.validate_term_contract", return_value=None):
                missing_policy = session.restore_readiness({
                    "known_year_term": {"identity_binding_verified": True},
                    "server_policy": {"verified": False},
                })
                self.assertEqual(missing_policy["code"], "policy_unverified")
                ready = session.restore_readiness({
                    "known_year_term": {"identity_binding_verified": True},
                    "server_policy": {"verified": True},
                })
            self.assertEqual(ready["code"], "ready_to_verify")
            self.assertTrue(ready["may_attempt"])
            self.assertFalse(ready["school_verified"])
            net.assert_not_called()

    def test_no_saved_session_never_advertises_passwordless_resume(self):
        session = LiteSession(vault=MemoryVault())
        ready = session.restore_readiness({
            "known_year_term": {"verified": True, "identity_binding_verified": True},
            "server_policy": {"verified": True},
        })
        self.assertEqual(ready["code"], "no_saved_session")
        self.assertFalse(ready["may_attempt"])

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

    def test_existing_login_manual_recheck_has_account_binding(self):
        session = LiteSession("JSESSIONID=private", "jwmis.ncwu.edu.cn", "verified",
                              username="student01")
        with patch("lite_session.validate_term_contract", return_value=None), \
             patch("lite_session.probe_known_year_term", new_callable=AsyncMock,
                   return_value=TermProbeResult("identity_verified")) as probe:
            result = session.check({"known_year_term": {"identity_binding_verified": True}})
        self.assertEqual(result["code"], "identity_verified")
        self.assertEqual(result["session"]["status"], "verified")
        self.assertEqual(probe.await_count, 1)
        self.assertEqual(probe.await_args.kwargs["expected_user_code"], "student01")
        self.assertNotIn("private", str(result))

    def test_manual_check_rejects_unverified_contract_without_network(self):
        session = LiteSession("JSESSIONID=private", "jwmis.ncwu.edu.cn", "verified",
                              username="student01")
        with patch("lite_session.probe_known_year_term", new_callable=AsyncMock) as probe:
            result = session.check({"known_year_term": {"identity_binding_verified": False}})
        self.assertEqual(result["code"], "verification_unavailable")
        probe.assert_not_called()

    def test_manual_check_rejects_wrong_account_and_revokes_encrypted_token(self):
        vault = MemoryVault(SavedSchoolSession("student01", "JSESSIONID=private", 0.0))
        session = LiteSession("JSESSIONID=private", "jwmis.ncwu.edu.cn", "verified",
                              vault=vault, username="student01")
        with patch("lite_session.validate_term_contract", return_value=None), \
             patch("lite_session.probe_known_year_term", new_callable=AsyncMock,
                   return_value=TermProbeResult("identity_mismatch")):
            result = session.check({"known_year_term": {"identity_binding_verified": True}})
        self.assertEqual(result["code"], "identity_mismatch")
        self.assertEqual(result["session"]["status"], "not_logged_in")
        self.assertFalse(result["session"]["saved_session_available"])

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

    def test_cookie_name_is_not_forced_to_jsessionid(self):
        # A legitimate same-origin school response can name its session
        # differently. The server identity read is what authorizes resume.
        self.vault.save("student01", "STUDENT_SESSION=abc123; SESSION=token456")
        restored = self.vault.load()
        self.assertIsNotNone(restored)
        self.assertEqual(restored.cookie, "STUDENT_SESSION=abc123; SESSION=token456")

    def test_dpapi_survives_separate_python_process(self):
        # Simulates closing and restarting the EXE as the same Windows user.
        # No school call or real credential is involved.
        import json
        self.vault.save("student01", "NCWU_SESSION=synthetic-token")
        path_literal = json.dumps(str(self.vault.directory))
        code = (
            "from pathlib import Path; "
            "from lite_session_vault import WindowsSessionVault; "
            f"v=WindowsSessionVault(Path({path_literal})); "
            "s=v.load(); "
            "assert s is not None and s.username == 'student01'; "
            "assert s.cookie == 'NCWU_SESSION=synthetic-token'; "
            "print('restored')"
        )
        output = subprocess.check_output(
            [sys.executable, "-c", code], timeout=15, text=True,
        )
        self.assertEqual(output.strip(), "restored")

    def test_duplicate_cookie_name_rejected(self):
        from lite_session_vault import _valid_cookie
        self.assertFalse(_valid_cookie("X=a; X=b"))
        self.assertFalse(_valid_cookie("X=a\r\nX-Injected=b"))
        self.assertFalse(_valid_cookie(""))

    def test_expired_encrypted_record_is_never_restored(self):
        with patch("lite_session_vault.time.time", return_value=1_700_000_000.0):
            self.vault.save("student01", "JSESSIONID=private")
        with patch("lite_session_vault.time.time", return_value=1_700_000_000.0 + 8*24*3600):
            self.assertIsNone(self.vault.load())

    def test_unencrypted_file_cannot_be_interpreted_as_login(self):
        self.vault.path.write_text('{"account":"student01","cookie":"JSESSIONID=secret"}')
        self.assertIsNone(self.vault.load())
