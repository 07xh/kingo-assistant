"""Compact sidebar visual shell preserves the old Notebook navigation contract."""
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from windows_visual_shell import (
    PALETTE, NAV_SECTIONS, apply_light_theme, make_visual_shell,
    connect_sidebar, sync_sidebar,
)


class ThemeConfigurationTests(TestCase):
    def test_theme_hides_old_tabs_and_styles_primary_actions(self):
        owner = Mock()
        style = Mock()
        style.theme_names.return_value = ("default", "clam")
        with patch("windows_visual_shell.ttk.Style", return_value=style):
            apply_light_theme(owner)
        style.theme_use.assert_called_once_with("clam")
        style.layout.assert_called_once_with(
            "Kingo.Content.TNotebook.Tab", [])
        style.configure.assert_any_call(
            "KingoPrimary.TButton",
            padding=(14, 9),
            font=("Microsoft YaHei UI", 9, "bold"),
            background=PALETTE["accent"],
            foreground=PALETTE["surface"],
            bordercolor=PALETTE["accent"],
            borderwidth=1, relief="flat",
        )
        owner.configure.assert_called_once_with(
            bg=PALETTE["background"])

    def test_sidebar_links_use_the_original_three_notebook_tabs(self):
        owner = Mock()
        owner.tab_home = "tab/home"
        owner.tab_manual = "tab/manual"
        owner.tab_tools = "tab/tools"
        buttons = []
        def button(*args, **kwargs):
            control = Mock()
            buttons.append((control, kwargs))
            return control
        with patch("windows_visual_shell.tk.Frame") as frame, \
             patch("windows_visual_shell.tk.Label") as label, \
             patch("windows_visual_shell.tk.Button", side_effect=button), \
             patch("windows_visual_shell.ttk.Frame") as content:
            result = make_visual_shell(owner)
        self.assertIs(result, content.return_value)
        self.assertEqual(set(owner.sidebar_buttons), {
            "home", "manual", "tools",
        })
        self.assertEqual(len(buttons), 3)
        self.assertIn("sidebar", owner.__dict__)
        self.assertIn("visual_content", owner.__dict__)
        for key, _glyph, name, target in NAV_SECTIONS:
            control = owner.sidebar_buttons[key]
            self.assertEqual(
                next(kwargs["text"] for button, kwargs in buttons
                     if button is control).split("\n")[-1], name)
            kwargs = next(kwargs for button, kwargs in buttons
                          if button is control)
            kwargs["command"]()
            owner.notebook.select.assert_called_with(
                getattr(owner, target))
        # A navigation button never opens the browser or calls school APIs.
        owner.start_native_login.assert_not_called()
        owner.open_official_portal.assert_not_called()

    def test_sidebar_highlight_tracks_programmatic_tab_navigation(self):
        owner = SimpleNamespace(
            tab_home="home-path", tab_manual="manual-path",
            tab_tools="tools-path", notebook=Mock(),
            sidebar_buttons={
                "home": Mock(), "manual": Mock(), "tools": Mock(),
            },
        )
        owner.notebook.select.return_value = "manual-path"
        sync_sidebar(owner)
        self.assertEqual(
            owner.sidebar_buttons["manual"].configure.call_args.kwargs["bg"],
            PALETTE["accent"])
        self.assertEqual(
            owner.sidebar_buttons["home"].configure.call_args.kwargs["bg"],
            PALETTE["sidebar"])
        self.assertEqual(
            owner.sidebar_buttons["tools"].configure.call_args.kwargs["bg"],
            PALETTE["sidebar"])
        owner.notebook.select.return_value = "tools-path"
        connect_sidebar(owner)
        args = owner.notebook.bind.call_args
        self.assertEqual(args.args[0], "<<NotebookTabChanged>>")
        self.assertEqual(args.kwargs["add"], "+")
        self.assertEqual(
            owner.sidebar_buttons["tools"].configure.call_args.kwargs["bg"],
            PALETTE["accent"])
        owner.notebook.select.return_value = "home-path"
        args.args[1](None)
        self.assertEqual(
            owner.sidebar_buttons["home"].configure.call_args.kwargs["bg"],
            PALETTE["accent"])


if __name__ == "__main__":
    import unittest
    unittest.main()
