"""Headless GUI copy action exports only schema report, never captured values."""
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from windows_gui import Application


class HARContractCopyGuiTests(TestCase):
    def test_copy_without_report_does_not_touch_clipboard(self):
        app = SimpleNamespace(
            clipboard_clear=Mock(), clipboard_append=Mock(),
            update_idletasks=Mock(), log=Mock())
        with patch("windows_gui.messagebox.showinfo") as message:
            Application.copy_ncwu_contract_report(app)
        message.assert_called_once()
        app.clipboard_clear.assert_not_called()
        app.clipboard_append.assert_not_called()

    def test_copy_uses_only_sanitized_report(self):
        app = SimpleNamespace(
            _last_ncwu_contract_report=(
                "轮次初始化：请求字段名：xn, xq\n"
                "响应字段名：status, result\n"
                "业务语义仍待验证。"),
            clipboard_clear=Mock(), clipboard_append=Mock(),
            update_idletasks=Mock(), log=Mock())
        with patch("windows_gui.messagebox.showinfo"):
            Application.copy_ncwu_contract_report(app)
        app.clipboard_clear.assert_called_once()
        app.clipboard_append.assert_called_once_with(
            app._last_ncwu_contract_report)
        app.update_idletasks.assert_called_once()

    def test_clipboard_failure_is_caught(self):
        import tkinter as tk
        app = SimpleNamespace(
            _last_ncwu_contract_report="xn, xq",
            clipboard_clear=Mock(side_effect=tk.TclError("not available")),
            clipboard_append=Mock(), update_idletasks=Mock(), log=Mock())
        with patch("windows_gui.messagebox.showwarning") as warn:
            Application.copy_ncwu_contract_report(app)
        warn.assert_called_once()
        app.clipboard_append.assert_not_called()
        app.log.assert_not_called()


if __name__ == "__main__":
    import unittest
    unittest.main()
