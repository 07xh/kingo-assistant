"""Dense desktop shell: theme contract, command strip and tab strip owner."""
from unittest import TestCase
from unittest.mock import Mock, patch

from windows_visual_shell import (
    PALETTE,
    TABS,
    apply_light_theme,
    make_visual_shell,
)


class ThemeConfigurationTests(TestCase):
    def test_theme_keeps_the_tab_strip_visible_and_styles_the_cta(self):
        owner = Mock()
        style = Mock()
        style.theme_names.return_value = ("default", "clam")
        with patch("windows_visual_shell.ttk.Style", return_value=style):
            apply_light_theme(owner)
        style.theme_use.assert_called_once_with("clam")
        # The previous shell hid the notebook strip with an empty layout.
        # Navigation is a real tab control again, so that call is a defect.
        style.layout.assert_not_called()
        style.configure.assert_any_call(
            "KingoPrimary.TButton", padding=(10, 5),
            font=("Microsoft YaHei UI", 9, "bold"),
            background=PALETTE["accent"], foreground=PALETTE["surface"],
            bordercolor=PALETTE["accent"], borderwidth=1, relief="solid")
        owner.configure.assert_called_once_with(bg=PALETTE["background"])

    def test_grid_density_and_tab_colours_are_pinned(self):
        owner = Mock()
        style = Mock()
        style.theme_names.return_value = ("clam",)
        with patch("windows_visual_shell.ttk.Style", return_value=style):
            apply_light_theme(owner)
        treeview = next(
            call for call in style.configure.call_args_list
            if call.args and call.args[0] == "Treeview")
        self.assertEqual(treeview.kwargs["rowheight"], 23)
        self.assertEqual(treeview.kwargs["background"], PALETTE["surface"])
        heading = next(
            call for call in style.configure.call_args_list
            if call.args and call.args[0] == "Treeview.Heading")
        self.assertEqual(heading.kwargs["background"], PALETTE["grid_header"])
        tab = next(
            call for call in style.configure.call_args_list
            if call.args and call.args[0] == "Kingo.Content.TNotebook.Tab")
        # clam needs explicit light/dark edges, otherwise every tab is flat.
        self.assertIn("lightcolor", tab.kwargs)
        self.assertIn("darkcolor", tab.kwargs)


class ShellConstructionTests(TestCase):
    def test_shell_owns_command_strip_status_cells_and_page_host(self):
        owner = Mock()
        owner.status = "status-var"
        frames = []
        labels = []
        content = Mock(name="content")

        def tk_frame(*args, **kwargs):
            widget = Mock(name="tk.Frame")
            frames.append((widget, args, kwargs))
            return widget

        def tk_label(*args, **kwargs):
            widget = Mock(name="tk.Label")
            labels.append((widget, kwargs))
            return widget

        def ttk_frame(*args, **kwargs):
            if kwargs.get("style") == "Shell.TFrame":
                return content
            return Mock(name="ttk.Frame")

        def ttk_label(*args, **kwargs):
            widget = Mock(name="ttk.Label")
            labels.append((widget, kwargs))
            return widget

        with patch("windows_visual_shell.tk.Frame", side_effect=tk_frame), \
             patch("windows_visual_shell.tk.Label", side_effect=tk_label), \
             patch("windows_visual_shell.ttk.Frame", side_effect=ttk_frame), \
             patch("windows_visual_shell.ttk.Label", side_effect=ttk_label):
            result = make_visual_shell(owner)

        self.assertIs(result, content)
        self.assertIs(owner.visual_content, content)
        strip = next(widget for widget, args, kwargs in frames
                     if kwargs.get("height") == 34)
        self.assertIs(owner.toolbar, strip)
        extras = next(widget for widget, args, kwargs in frames
                      if args and args[0] is strip
                      and kwargs == {"bg": PALETTE["panel"]})
        self.assertIs(owner.toolbar_extras, extras)
        status_label = next(
            widget for widget, kwargs in labels
            if kwargs.get("textvariable") == "status-var")
        self.assertIs(owner.status_bar, status_label)
        self.assertEqual(
            next(kwargs for widget, kwargs in labels
                 if widget is status_label)["style"],
            "KingoStatus.TLabel")
        self.assertEqual(
            [kwargs["text"] for _, kwargs in labels
             if "text" in kwargs][:2],
            ["KingoAssistant", "华水选课助手 · 只读监测"])

    def test_tab_table_is_the_single_source_of_tab_captions(self):
        self.assertEqual([attribute for attribute, _ in TABS],
                         ["tab_home", "tab_tools"])
        self.assertEqual([caption for _, caption in TABS], [
            "选课工作台", "工具与日志",
        ])

    def test_navigation_no_longer_depends_on_a_hidden_sidebar(self):
        import windows_visual_shell as module

        self.assertFalse(hasattr(module, "connect_sidebar"))
        self.assertFalse(hasattr(module, "sync_sidebar"))
        self.assertNotIn("sidebar", module.PALETTE)
        owner = Mock()
        owner.status = "status-var"
        with patch("windows_visual_shell.tk.Frame", Mock()), \
             patch("windows_visual_shell.tk.Label", Mock()), \
             patch("windows_visual_shell.ttk.Frame", Mock()), \
             patch("windows_visual_shell.ttk.Label", Mock()):
            make_visual_shell(owner)
        self.assertNotIn("sidebar", owner.__dict__)


class ShellModuleHygieneTests(TestCase):
    def test_shell_imports_no_network_or_application_code(self):
        import windows_visual_shell as module

        for forbidden in ("httpx", "windows_gui", "core.client", "socket"):
            self.assertNotIn(forbidden, module.__dict__)
        self.assertIsInstance(module.PALETTE, dict)
        self.assertIn("accent", module.PALETTE)


if __name__ == "__main__":
    import unittest
    unittest.main()
