"""Responsive shell behaviour after the sidebar was replaced by a strip.

The window no longer shrinks a dark rail; it collapses the secondary
commands, re-wraps the status text and clamps long help labels so the dense
grid keeps its columns.
"""
from tkinter import ttk
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

from windows_visual_shell import (
    bind_page_wheel,
    bind_responsive_shell,
    make_scrollable_content,
    update_responsive_shell,
)


class ResponsiveShellTests(TestCase):
    def make_owner(self, width=1200, height=800):
        owner = Mock()
        owner.winfo_width.return_value = width
        owner.winfo_height.return_value = height
        owner.toolbar_extras.winfo_manager.return_value = "pack"
        for name in ("tab_home", "tab_manual", "tab_tools"):
            getattr(owner, name).winfo_children.return_value = []
        return owner

    def test_narrow_window_collapses_secondary_commands(self):
        owner = self.make_owner(width=900, height=820)
        update_responsive_shell(owner)
        owner.toolbar_extras.pack_forget.assert_called_once()
        owner.status_bar.configure.assert_called_once_with(wraplength=640)
        self.assertEqual(owner._shell_height_mode, "regular")

    def test_wide_window_restores_the_secondary_commands(self):
        owner = self.make_owner(width=1400, height=600)
        owner.toolbar_extras.winfo_manager.return_value = ""
        update_responsive_shell(owner)
        owner.toolbar_extras.pack.assert_called_once_with(
            side="right", padx=(0, 6))
        owner.status_bar.configure.assert_called_once_with(wraplength=1140)
        self.assertEqual(owner._shell_height_mode, "short")

    def test_status_width_never_collapses_below_a_readable_floor(self):
        owner = self.make_owner(width=320, height=800)
        update_responsive_shell(owner)
        self.assertEqual(
            owner.status_bar.configure.call_args.kwargs["wraplength"], 320)

    def test_child_configure_event_does_not_relayout_main_window(self):
        owner = self.make_owner()
        update_responsive_shell(owner, SimpleNamespace(widget=object()))
        owner.toolbar_extras.pack_forget.assert_not_called()
        owner.status_bar.configure.assert_not_called()

    def test_long_help_labels_are_clamped_once_per_width(self):
        owner = self.make_owner(width=1000, height=800)
        # spec makes isinstance(widget, ttk.Label) true, which is how the
        # shell decides what to clamp.
        label = Mock(spec=ttk.Label)
        label.cget.return_value = 980
        label.winfo_children.return_value = []
        owner.tab_home.winfo_children.return_value = [label]
        update_responsive_shell(owner)
        label.configure.assert_called_once_with(wraplength=910)
        # The same width on the next Configure event must not re-wrap it.
        update_responsive_shell(owner)
        label.configure.assert_called_once()

    def test_binding_uses_original_tk_configure_event(self):
        owner = self.make_owner()
        bind_responsive_shell(owner)
        args = owner.bind.call_args
        self.assertEqual(args.args[0], "<Configure>")
        self.assertEqual(args.kwargs, {"add": "+"})
        owner.status_bar.configure.reset_mock()
        args.args[1](SimpleNamespace(widget=owner))
        owner.status_bar.configure.assert_called_once()


class PageScrollingTests(TestCase):
    def test_scroll_content_is_embedded_in_canvas_with_vertical_scrollbar(self):
        with patch("windows_visual_shell.ttk.Frame") as frame, \
             patch("windows_visual_shell.ttk.Scrollbar") as bar, \
             patch("windows_visual_shell.tk.Canvas") as canvas:
            body, viewport, scrollbar = make_scrollable_content(Mock())
        self.assertIs(body, frame.return_value)
        self.assertIs(viewport, canvas.return_value)
        self.assertIs(scrollbar, bar.return_value)
        canvas.return_value.create_window.assert_called_once_with(
            (0, 0), window=body, anchor="nw")
        self.assertEqual(
            canvas.return_value.bind.call_args.args[0], "<Configure>")
        self.assertEqual(
            body.bind.call_args.args[0], "<Configure>")
        bar.return_value.configure.assert_called_once_with(
            command=canvas.return_value.yview)

    def test_passive_widgets_scroll_outer_canvas_not_nested_table(self):
        canvas = Mock()
        passive = Mock()
        passive.winfo_class.return_value = "TLabel"
        passive.winfo_children.return_value = []
        course_table = Mock()
        course_table.winfo_class.return_value = "Treeview"
        course_table.winfo_children.return_value = []
        root = Mock()
        root.winfo_class.return_value = "TFrame"
        root.winfo_children.return_value = [passive, course_table]
        bind_page_wheel(canvas, root)
        self.assertEqual(passive.bind.call_count, 3)
        course_table.bind.assert_not_called()
        cb = next(call.args[1] for call in passive.bind.call_args_list
                  if call.args[0] == "<MouseWheel>")
        cb(SimpleNamespace(delta=-240, num=None))
        canvas.yview_scroll.assert_called_once_with(2, "units")
        canvas.yview_scroll.reset_mock()
        cb(SimpleNamespace(delta=120, num=None))
        canvas.yview_scroll.assert_called_once_with(-1, "units")
        cb4 = next(call.args[1] for call in passive.bind.call_args_list
                   if call.args[0] == "<Button-4>")
        canvas.yview_scroll.reset_mock()
        cb4(SimpleNamespace(num=4, delta=0))
        canvas.yview_scroll.assert_called_once_with(-3, "units")


if __name__ == "__main__":
    import unittest
    unittest.main()
