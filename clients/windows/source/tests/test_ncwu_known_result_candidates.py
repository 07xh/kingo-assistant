"""Synthetic historical selected-class table identity candidates only."""
from unittest import TestCase

from core.ncwu_known_result_candidates import inspect_known_result_candidates


def document(*, rows=None, header=True):
    titles = ("<tr><td>课程</td><td>上课班级 代码</td>"
              "<td>选课 状态</td></tr>" if header else "")
    rows = rows if rows is not None else (
        '<tr><td>虚构课程</td><td>展示编号</td><td>显示状态</td>'
        '<td name="kcdm" id="kcdm_row">C101</td>'
        '<td name="skbjdm" id="skbjdm_row">INTERNAL-CLASS</td></tr>'
    )
    return "<html><body><table>" + titles + rows + "</table></body></html>"


class HistoricalSelectedTests(TestCase):
    def test_exact_named_cells_are_extracted_without_claiming_success(self):
        result = inspect_known_result_candidates(document())
        self.assertEqual(result.recognized_tables, 1)
        self.assertEqual(result.candidate_rows[0].course_code, "C101")
        self.assertEqual(result.candidate_rows[0].internal_class_id, "INTERNAL-CLASS")
        self.assertIn("不能用于自动提交或确认选课成功", result.message)
        self.assertNotIn("INTERNAL-CLASS", result.message)

    def test_display_class_without_internal_named_cell_is_not_accepted(self):
        result = inspect_known_result_candidates(document(rows=(
            "<tr><td>虚构课程</td><td>VISIBLE-SKBH</td><td>显示状态</td>"
            '<td name="kcdm">C101</td></tr>')))
        self.assertEqual(result.candidate_rows, ())
        self.assertEqual(result.ignored_rows, 1)

    def test_duplicate_internal_fields_and_bad_values_are_rejected(self):
        rows = (
            '<tr><td>A</td><td>B</td><td>C</td>'
            '<td name="kcdm">C101</td><td name="skbjdm">INTERNAL</td>'
            '<td name="skbjdm">SECOND</td></tr>'
            '<tr><td>A</td><td>B</td><td>C</td>'
            '<td name="kcdm"></td><td name="skbjdm">INTERNAL</td></tr>'
        )
        result = inspect_known_result_candidates(document(rows=rows))
        self.assertEqual(result.candidate_rows, ())
        self.assertEqual(result.ignored_rows, 2)

    def test_incorrect_or_nested_table_does_not_get_class_identity(self):
        not_selected = document(header=False)
        self.assertEqual(inspect_known_result_candidates(not_selected).recognized_tables, 0)
        nested = ("<html><body><table><tr><td>"
                  + document() + "</td></tr></table></body></html>")
        result = inspect_known_result_candidates(nested)
        self.assertEqual(result.recognized_tables, 1)
        self.assertEqual(len(result.candidate_rows), 1)

    def test_login_page_and_invalid_document_produce_no_candidates(self):
        for bad in ('<html><input type="password">' + document(),
                    "unexpected text", "x"*(512*1024+1)):
            with self.subTest(kind=len(bad)):
                self.assertEqual(inspect_known_result_candidates(bad).candidate_rows, ())

    def test_duplicate_rows_do_not_create_fake_multiple_selection(self):
        row = ('<tr><td>课程</td><td>班级</td><td>状态</td>'
               '<td name="kcdm">C101</td><td name="skbjdm">INTERNAL</td></tr>')
        result = inspect_known_result_candidates(document(rows=row+row))
        self.assertEqual(len(result.candidate_rows), 1)
