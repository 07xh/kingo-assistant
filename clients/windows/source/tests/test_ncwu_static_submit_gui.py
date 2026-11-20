"""Headless GUI tests: saved static sources never trigger school requests."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from windows_gui import Application
from core.ncwu_submission_transport import SUBMIT_PATH


class S202StaticSubmitGuiTests(TestCase):
    def test_explicit_saved_file_returns_redacted_report(self):
        secret = "PRIVATE_ACTIVE_TOKEN_DO_NOT_COPY"
        with TemporaryDirectory() as folder:
            file = Path(folder) / "saved-selection.html"
            file.write_text(
                '<form method="post" action="' + SUBMIT_PATH + '">'
                '<input name="xh" value="' + secret + '"></form>',
                encoding="utf-8",
            )
            app = SimpleNamespace(log=Mock(), _last_ncwu_contract_report=None)
            with patch("windows_gui.filedialog.askopenfilenames",
                       return_value=(str(file),)), \
                 patch("windows_gui.messagebox.showinfo") as info, \
                 patch("windows_gui.messagebox.showwarning") as warning:
                Application.inspect_ncwu_static_submit_files(app)
            info.assert_called_once()
            warning.assert_not_called()
            app.log.assert_called_once()
            self.assertIn("POST form：1", app._last_ncwu_contract_report)
            self.assertNotIn(secret, app._last_ncwu_contract_report)
            self.assertIn("没有发送任何网络请求", app._last_ncwu_contract_report)

    def test_missing_or_oversize_file_clears_previous_report(self):
        with TemporaryDirectory() as folder:
            file = Path(folder) / "too-big.js"
            file.write_bytes(b"x" * (2 * 1024 * 1024 + 1))
            app = SimpleNamespace(
                log=Mock(), _last_ncwu_contract_report="old evidence")
            with patch("windows_gui.filedialog.askopenfilenames",
                       return_value=(str(file),)), \
                 patch("windows_gui.messagebox.showinfo") as info, \
                 patch("windows_gui.messagebox.showwarning") as warning:
                Application.inspect_ncwu_static_submit_files(app)
            self.assertIsNone(app._last_ncwu_contract_report)
            warning.assert_called_once()
            info.assert_not_called()
            app.log.assert_not_called()


if __name__ == "__main__":
    import unittest
    unittest.main()
