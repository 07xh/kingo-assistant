"""Synthetic offline-only static submit contract evidence tests."""
import unittest

from core.ncwu_static_submit_evidence import inspect_static_submit_sources
from core.ncwu_submission_transport import SUBMIT_PATH

SECRET = "NEVER_EXPOSE_ACTIVE_SECRET"
HOST = "https://jwmis.ncwu.edu.cn"


class StaticS202SubmitEvidenceTests(unittest.TestCase):
    def test_exact_first_party_post_form_reports_field_names_not_values(self):
        fields = ("xh", "xn", "xq", "lcid", "xktype", "hidOption", "items")
        inputs = "".join(
            f'<input name="{name}" value="{SECRET}">' for name in fields
        )
        html = (
            '<form method="POST" action="' + HOST + SUBMIT_PATH + '?ticket='
            + SECRET + '">' + inputs + '</form>'
        ).encode("utf-8")
        report = inspect_static_submit_sources([(".html", html)])
        self.assertEqual(report.code, "parsed")
        self.assertEqual(report.target_form_count, 1)
        self.assertEqual(report.evidence_state, "form_fields_observed")
        self.assertEqual(set(report.form_field_names), set(fields))
        self.assertNotIn(SECRET, repr(report) + report.message)
        self.assertIn("没有发送任何网络请求", report.message)

    def test_js_mention_is_only_a_clue_not_a_verified_form(self):
        report = inspect_static_submit_sources([
            (".js", ('// "' + SUBMIT_PATH + '" is an old example').encode()),
        ])
        self.assertEqual(report.path_reference_count, 1)
        self.assertEqual(report.target_form_count, 0)
        self.assertEqual(report.evidence_state, "text_reference_only")
        self.assertEqual(report.form_field_names, ())

    def test_external_or_nonpost_forms_are_not_trusted(self):
        html = (
            '<form method="POST" action="https://evil.invalid'
            + SUBMIT_PATH + '"><input name="items" value="' + SECRET + '"></form>'
            + '<form method="GET" action="' + SUBMIT_PATH
            + '"><input name="xh"></form>'
        ).encode("utf-8")
        report = inspect_static_submit_sources([(".html", html)])
        self.assertEqual(report.target_form_count, 0)
        self.assertEqual(report.form_field_names, ())
        self.assertEqual(report.evidence_state, "text_reference_only")
        self.assertNotIn(SECRET, report.message)

    def test_relative_exact_post_path_is_accepted(self):
        html = ('<form method="post" action="' + SUBMIT_PATH
                + '"><input name="xh"></form>').encode()
        report = inspect_static_submit_sources([(".html", html)])
        self.assertEqual(report.target_form_count, 1)
        self.assertEqual(report.evidence_state, "partial_form_observed")
        self.assertEqual(report.form_field_names, ("xh",))

    def test_invalid_sources_fail_closed_without_partial_results(self):
        valid = (".js", SUBMIT_PATH.encode())
        cases = [
            [],
            [(".txt", b"hello")],
            [(".html", b"")],
            [(".js", b"x" * (2 * 1024 * 1024 + 1))],
            [valid] * 13,
            [valid, (".exe", b"not a source")],
        ]
        for sources in cases:
            with self.subTest(sources=len(sources)):
                report = inspect_static_submit_sources(sources)
                self.assertEqual(report.code, "invalid")
                self.assertEqual(report.target_form_count, 0)
                self.assertEqual(report.path_reference_count, 0)


if __name__ == "__main__":
    unittest.main()
