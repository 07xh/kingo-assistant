"""Native Tk table and popup deduplication keeps widget contracts stable."""
from unittest import TestCase
from unittest.mock import Mock, patch

from windows_gui import _create_readonly_tree, _open_readonly_popup


class NativeReadonlyTableTests(TestCase):
    def test_column_names_labels_widths_and_height_are_preserved(self):
        parent = Mock()
        columns = ("name", "code", "class")
        titles = ("课程", "课程代码", "教学班")
        widths = (165, 120, 150)
        with patch("windows_gui.ttk.Treeview") as tree:
            result = _create_readonly_tree(
                parent, columns=columns, titles=titles,
                widths=widths, minwidth=55, height=17,
            )
        self.assertIs(result, tree.return_value)
        tree.assert_called_once_with(
            parent, columns=columns, show="headings", height=17,
        )
        self.assertEqual(
            [call.args for call in result.heading.call_args_list],
            [("name",), ("code",), ("class",)],
        )
        self.assertEqual(
            [call.kwargs for call in result.column.call_args_list],
            [{"width": 165, "minwidth": 55},
             {"width": 120, "minwidth": 55},
             {"width": 150, "minwidth": 55}],
        )
        self.assertEqual(
            [call.kwargs["text"] for call in result.heading.call_args_list],
            list(titles),
        )

    def test_candidate_table_keeps_default_height(self):
        with patch("windows_gui.ttk.Treeview") as tree:
            _create_readonly_tree(
                Mock(), columns=("name", "teacher"),
                titles=("课程", "教师"), widths=(180, 180), minwidth=110,
            )
        self.assertNotIn("height", tree.call_args.kwargs)

    def test_bad_column_schema_fails_before_widget_creation(self):
        with patch("windows_gui.ttk.Treeview") as tree:
            with self.assertRaises(ValueError):
                _create_readonly_tree(
                    Mock(), columns=("a", "b"), titles=("A",),
                    widths=(100, 100), minwidth=55,
                )
        tree.assert_not_called()

    def test_shared_popup_preserves_title_size_and_message(self):
        owner = Mock()
        with patch("windows_gui.tk.Toplevel") as toplevel, \
             patch("windows_gui.ttk.Label") as label:
            popup = _open_readonly_popup(
                owner, title="只读候选", size="760x400",
                message="尚待官网核对", wraplength=720,
            )
        self.assertIs(popup, toplevel.return_value)
        toplevel.assert_called_once_with(owner)
        popup.title.assert_called_once_with("只读候选")
        popup.geometry.assert_called_once_with("760x400")
        popup.transient.assert_called_once_with(owner)
        label.assert_called_once_with(
            popup, text="尚待官网核对", wraplength=720,
        )
        label.return_value.pack.assert_called_once_with(
            anchor="w", padx=12, pady=(12, 8),
        )


if __name__ == "__main__":
    import unittest
    unittest.main()
