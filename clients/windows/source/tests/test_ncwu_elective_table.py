"""Purely synthetic tests: no school HTTP, credentials, or real course names."""
import unittest

from core.ncwu_elective_table import EXPECTED_HEADINGS, inspect_elective_table


def sample_html(rows=1):
    headers = "".join(f'<th id="{key}">{key}</th>' for key in EXPECTED_HEADINGS)
    entries = "".join("<tr><td>虚构课程</td><td>仅供测试</td></tr>" for _ in range(rows))
    return ("<html><meta charset='GBK'><table><tr>" + headers
            + "</tr>" + entries + "</table></html>").encode("gbk")


class ElectiveTableTests(unittest.TestCase):
    def test_gbk_full_header_is_only_structure_evidence(self):
        result = inspect_elective_table(sample_html(2), "text/html; charset=GBK")
        self.assertEqual(result.code, "header_observed")
        self.assertEqual(result.recognized_headers, 17)
        self.assertEqual(result.candidate_data_rows, 2)
        self.assertNotIn("虚构课程", str(result))
        self.assertIn("尚未确认", result.message)

    def test_header_only_closed_window_is_not_available_course(self):
        result = inspect_elective_table(sample_html(0))
        self.assertEqual(result.code, "header_observed")
        self.assertEqual(result.candidate_data_rows, 0)
        self.assertFalse(hasattr(result, "remaining_seats"))

    def test_expired_credentials_are_not_a_course_table(self):
        result = inspect_elective_table("凭证已失效".encode("gbk"))
        self.assertEqual(result.code, "expired")

    def test_missing_header_is_rejected(self):
        result = inspect_elective_table(b"<table><tr><td>hello</td></tr></table>")
        self.assertEqual(result.code, "schema_unverified")

    def test_partial_headings_are_not_accepted(self):
        raw = sample_html().replace(b'id="head_kcdm"', b'id="wrong_col"')
        result = inspect_elective_table(raw)
        self.assertEqual(result.code, "header_incomplete")

    def test_conflicting_charset_and_oversize_are_rejected(self):
        self.assertEqual(inspect_elective_table(
            sample_html(), "text/html; charset=UTF-8").code, "invalid_response")
        self.assertEqual(inspect_elective_table(b"a" * (2*1024*1024+1)).code,
                         "invalid_response")

    def test_ambiguous_tables_are_rejected(self):
        text = sample_html().decode("gbk")
        inner = text[text.index("<table>"):text.index("</table>")+8]
        duplicated = text.replace("</html>", inner + "</html>")
        self.assertEqual(inspect_elective_table(duplicated.encode("gbk")).code,
                         "ambiguous_table")


if __name__ == "__main__":
    unittest.main()
