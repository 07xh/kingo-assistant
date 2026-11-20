"""Offline contract tests: never send a real NCWU enrollment."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from hashlib import sha256
from urllib.parse import parse_qs
import unittest

import httpx

from core.enrollment import EnrollmentNotReady, SubmitReceipt
from core.ncwu_submission_transport import (
    PreparedNCWUSubmission,
    SUBMIT_PATH,
    submit_prepared_s202_once,
)


class FakeAttestations:
    def __init__(self):
        self.gates = []

    def require(self, name):
        self.gates.append(name)


class FakeBudget:
    def __init__(self):
        self.calls = 0

    @asynccontextmanager
    async def request(self):
        self.calls += 1
        yield


class SubmissionTransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.evidence = b"fictional local class-to-items mapping; NOT university proof"
        self.form = (
            ("xktype", "2"),
            ("xh", "OWN-LOCAL-ID"),
            ("xn", "2026"),
            ("xq", "0"),
            ("lcid", "ROUND-1"),
            ("hidOption", "SERVER-GENERATED-OPTION"),
            ("items", "SERVER-GENERATED-SINGLE-ITEM"),
        )
        self.prepared = PreparedNCWUSubmission(
            fields=self.form,
            internal_student_id="OWN-LOCAL-ID",
            academic_year="2026",
            academic_term="0",
            round_id="ROUND-1",
            target_course_code="COURSE-LOCAL",
            target_teaching_class_id="INTERNAL-CLASS-ID",
            selected_item="SERVER-GENERATED-SINGLE-ITEM",
            item_binding_sha256=sha256(self.evidence).hexdigest(),
        )
        self.calls = []
        self.attestations = FakeAttestations()
        self.budget = FakeBudget()

    def client_factory(self, status=200, body=b"flag:1,opt:1"):
        def handler(request):
            self.calls.append(request)
            return httpx.Response(status, content=body)
        return lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            follow_redirects=False,
            trust_env=False,
        )

    def settings(self, **changes):
        return {
            "config": {"base_url": "https://jwmis.ncwu.edu.cn"},
            "session_cookie": "JSESSIONID=synthetic-session",
            "prepared": self.prepared,
            "expected_course_code": "COURSE-LOCAL",
            "expected_internal_class_id": "INTERNAL-CLASS-ID",
            "current_internal_student_id": "OWN-LOCAL-ID",
            "current_academic_year": "2026",
            "current_academic_term": "0",
            "current_round_id": "ROUND-1",
            "item_binding_evidence": self.evidence,
            "attestations": self.attestations,
            "user_opted_in": True,
            "institution_permits_write": True,
            "request_budget": self.budget,
            "http_factory": self.client_factory(),
            **changes,
        }

    async def test_one_post_is_only_a_receipt_not_success(self):
        result = await submit_prepared_s202_once(**self.settings())
        self.assertEqual(result, SubmitReceipt(None))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.budget.calls, 1)
        request = self.calls[0]
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.url.path, SUBMIT_PATH)
        self.assertEqual(request.url.host, "jwmis.ncwu.edu.cn")
        self.assertEqual(request.headers["referer"],
                         "https://jwmis.ncwu.edu.cn/hsjw/frame/homes.action")
        fields = parse_qs(request.content.decode("ascii"))
        self.assertEqual(fields["items"], ["SERVER-GENERATED-SINGLE-ITEM"])
        self.assertEqual(fields["lcid"], ["ROUND-1"])
        self.assertEqual(len(self.attestations.gates), 4)

    async def test_never_sends_without_authorization_or_verified_budget(self):
        bad = [
            {"user_opted_in": False},
            {"institution_permits_write": False},
            {"attestations": None},
            {"request_budget": None},
            {"item_binding_evidence": b"other evidence"},
            {"current_round_id": "STALE-ROUND"},
            {"current_internal_student_id": "OTHER-ID"},
            {"current_academic_term": "1"},
            {"expected_course_code": "OTHER-COURSE"},
            {"expected_internal_class_id": "VISIBLE-SKBH-NOT-ID"},
            {"session_cookie": "JSESSIONID="},
            {"config": {"base_url": "http://jwmis.ncwu.edu.cn"}},
            {"config": {"base_url": "https://evil.example.invalid"}},
        ]
        for case in bad:
            with self.subTest(case=case):
                with self.assertRaises(EnrollmentNotReady):
                    await submit_prepared_s202_once(**self.settings(**case))
        self.assertEqual(self.calls, [])
        self.assertEqual(self.budget.calls, 0)

    async def test_form_must_be_unique_current_round_single_item(self):
        bad_forms = [
            self.form + (("items", "duplicate"),),
            tuple((k, "0") if k == "xktype" else (k, v) for k, v in self.form),
            tuple((k, "") if k == "hidOption" else (k, v) for k, v in self.form),
            tuple((k, "other") if k == "xh" else (k, v) for k, v in self.form),
            tuple((k, "item-1|item-2") if k == "items" else (k, v) for k, v in self.form),
        ]
        for form in bad_forms:
            with self.subTest(form=form), self.assertRaises(ValueError):
                PreparedNCWUSubmission(
                    form, "OWN-LOCAL-ID", "2026", "0", "ROUND-1",
                    "COURSE-LOCAL", "INTERNAL-CLASS-ID",
                    "SERVER-GENERATED-SINGLE-ITEM",
                    sha256(self.evidence).hexdigest(),
                )
        self.assertEqual(self.calls, [])

    async def test_prepared_form_redacts_identity_and_dynamic_selection_items(self):
        rendered = repr(self.prepared)
        self.assertEqual(rendered, "PreparedNCWUSubmission(<redacted>)")
        for sensitive in (
            "OWN-LOCAL-ID", "SERVER-GENERATED-OPTION",
            "SERVER-GENERATED-SINGLE-ITEM", "ROUND-1",
        ):
            self.assertNotIn(sensitive, rendered)
            self.assertNotIn(sensitive, str(self.prepared))

    async def test_form_rejects_control_characters_without_school_requests(self):
        for bad in ("HAS\\x00NULL", "HAS\\rCR", "HAS\\nLF", "HAS\\x7fDEL"):
            # Use actual control characters, not an escaped textual sequence.
            value = bad.encode("ascii").decode("unicode_escape")
            fields = tuple(
                (k, value) if k == "hidOption" else (k, v)
                for k, v in self.form
            )
            with self.subTest(bad=repr(value)), self.assertRaises(ValueError):
                PreparedNCWUSubmission(
                    fields, "OWN-LOCAL-ID", "2026", "0", "ROUND-1",
                    "COURSE-LOCAL", "INTERNAL-CLASS-ID",
                    "SERVER-GENERATED-SINGLE-ITEM",
                    sha256(self.evidence).hexdigest(),
                )
        self.assertEqual(self.calls, [])

    async def test_redirect_or_empty_response_is_unknown_not_retry(self):
        for status, body in ((302, b"redirect"), (200, b"")):
            self.calls.clear()
            with self.subTest(status=status, body=body):
                with self.assertRaises(EnrollmentNotReady):
                    await submit_prepared_s202_once(
                        **self.settings(http_factory=self.client_factory(status, body)))
                self.assertEqual(len(self.calls), 1)


if __name__ == "__main__":
    unittest.main()
