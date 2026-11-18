"""Offline integration tests for the real engine -> NCWU write transport seam."""
from __future__ import annotations

from contextlib import asynccontextmanager
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase

import httpx

from core.course_models import CourseTarget
from core.enrollment import AttemptJournal, EnrollmentEngine, EnrollmentNotReady, SubmitReceipt, WindowState
from core.ncwu_submission_transport import PreparedNCWUSubmission
from core.ncwu_submission_gateway import NCWUEvidenceBoundGateway, make_ncwu_enrollment_engine


class FakeSource:
    official_catalog_verified = True
    official_enrollment_verified = True
    official_window_verified = True
    official_write_verified = True
    account_scope = "account-test"
    enrollment_semester = "2026-0"
    verified_school_origin = "https://jwmis.ncwu.edu.cn"
    current_internal_student_id = "OWNER"
    current_academic_year = "2026"
    current_academic_term = "0"
    current_round_id = "ROUND"
    session_cookie = "JSESSIONID=synthetic"

    async def selection_window(self):
        return WindowState.OPEN

    async def read_enrollments(self):
        return set()

    async def list_offerings(self):
        return []

    async def read_schedule(self):
        return None


class Attest:
    def require(self, name):
        return None


class Budget:
    @asynccontextmanager
    async def request(self):
        yield


class GatewayTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory()
        self.addAsyncCleanup(self.cleanup_dir)
        self.target = CourseTarget(
            "first", "COURSE-ID", "INTERNAL-CLASS", semester="2026-0")
        self.journal = AttemptJournal(
            Path(self.temp.name) / "pending.json", "2026-0", "account-test")
        self.source = FakeSource()
        self.evidence = b"offline synthetic packet mapping, not university data"
        self.sent = []
        self.preparer_called = 0
        self.prepared = PreparedNCWUSubmission(
            (
                ("xktype", "2"), ("xh", "OWNER"), ("xn", "2026"),
                ("xq", "0"), ("lcid", "ROUND"),
                ("hidOption", "UNVERIFIED-EXAMPLE"),
                ("items", "SINGLE-ITEM-EXAMPLE"),
            ),
            "OWNER", "2026", "0", "ROUND", "COURSE-ID", "INTERNAL-CLASS",
            "SINGLE-ITEM-EXAMPLE", sha256(self.evidence).hexdigest(),
        )

    async def cleanup_dir(self):
        self.temp.cleanup()

    async def prepare(self, _):
        self.preparer_called += 1
        return self.prepared, self.evidence

    def factory(self):
        def on_post(request):
            self.sent.append(request)
            return httpx.Response(200, content=b"flag:1,opt:1")
        return httpx.AsyncClient(
            transport=httpx.MockTransport(on_post),
            follow_redirects=False, trust_env=False,
        )

    def bridge(self, **overrides):
        opts = {
            "source": self.source,
            "journal": self.journal,
            "form_provider": self.prepare,
            "attestations": Attest(),
            "config": {"base_url": "https://jwmis.ncwu.edu.cn"},
            "request_budget": Budget(),
            "user_opted_in": True,
            "institution_permits_write": True,
            "http_factory": self.factory,
        }
        opts.update(overrides)
        return NCWUEvidenceBoundGateway(**opts)

    async def test_engine_builder_exposes_gateway_without_auto_registering(self):
        engine = make_ncwu_enrollment_engine(
            source=self.source, journal=self.journal,
            form_provider=self.prepare, attestations=Attest(),
            config={"base_url": "https://jwmis.ncwu.edu.cn"},
            request_budget=Budget(), user_opted_in=True,
            institution_permits_write=True, http_factory=self.factory,
        )
        self.assertIsInstance(engine, EnrollmentEngine)
        engine.validate_ready()
        self.assertEqual(self.sent, [])

    async def test_durable_intent_must_exist_before_post(self):
        gateway = self.bridge()
        with self.assertRaises(EnrollmentNotReady):
            await gateway.submit_once(self.target)
        self.assertEqual(self.preparer_called, 0)
        self.assertEqual(self.sent, [])

    async def test_offline_mock_post_uses_engine_receipt_unknown(self):
        gateway = self.bridge()
        self.journal.reserve_before_write(self.target)
        reply = await gateway.submit_once(self.target)
        self.assertEqual(reply, SubmitReceipt(None))
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0].url.path, "/hsjw/STU_ElectCourseAction.do")

    async def test_mapping_or_changed_session_never_submits(self):
        self.journal.reserve_before_write(self.target)
        wrong = PreparedNCWUSubmission(
            self.prepared.fields, "OWNER", "2026", "0", "ROUND",
            "COURSE-ID", "OTHER-CLASS",
            "SINGLE-ITEM-EXAMPLE", sha256(self.evidence).hexdigest(),
        )

        async def bad_map(_):
            return wrong, self.evidence

        with self.assertRaises(EnrollmentNotReady):
            await self.bridge(form_provider=bad_map).submit_once(self.target)
        self.assertEqual(self.sent, [])

        async def switched(_):
            self.source.current_round_id = "OTHER-ROUND"
            return self.prepared, self.evidence

        with self.assertRaises(EnrollmentNotReady):
            await self.bridge(form_provider=switched).submit_once(self.target)
        self.assertEqual(self.sent, [])

    async def test_unverified_source_never_reads_or_sends(self):
        self.journal.reserve_before_write(self.target)
        self.source.official_write_verified = False
        with self.assertRaises(EnrollmentNotReady):
            await self.bridge().submit_once(self.target)
        self.assertEqual(self.preparer_called, 0)
        self.assertEqual(self.sent, [])
