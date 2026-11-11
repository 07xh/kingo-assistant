"""No university traffic: official channel registry and entry routing gates."""
from __future__ import annotations

import asyncio
import unittest

import httpx

from core.official_entries import (
    CatalogObservation, CatalogState, EntryKind, OfficialEntry,
    OFFICIAL_ENTRIES, ProbeState, choose_readonly_catalog_route,
    probe_official_login_surfaces,
)


class InventoryTest(unittest.TestCase):
    def test_only_the_documented_web_direct_entry_can_be_catalog_candidate(self):
        self.assertEqual(
            [e.identifier for e in OFFICIAL_ENTRIES if e.catalog_candidate],
            ["ncwu_web"]
        )
        self.assertEqual(
            [e.identifier for e in OFFICIAL_ENTRIES if e.launch_url is None],
            ["ncwu_xiqueer"]
        )
        self.assertFalse(any(
            e.catalog_candidate for e in OFFICIAL_ENTRIES
            if e.kind in (EntryKind.SSO_BROWSER, EntryKind.MOBILE_APP)
        ))


class ManualProbeTest(unittest.IsolatedAsyncioTestCase):
    async def test_unverified_origins_skip_all_real_network_requests(self):
        from unittest.mock import patch
        with patch("core.official_entries.httpx.AsyncClient") as client:
            client.return_value.__aenter__.return_value.stream.side_effect = AssertionError(
                "School network must never be reached without a verified budget"
            )
            report = await probe_official_login_surfaces()
            client.return_value.__aenter__.return_value.stream.assert_not_called()
        self.assertEqual([r.state for r in report], [
            ProbeState.POLICY_UNVERIFIED, ProbeState.POLICY_UNVERIFIED,
            ProbeState.APP_ONLY,
        ])

    async def test_probe_is_explicit_single_get_per_web_login_no_redirects_or_cookies(self):
        seen = []

        def handler(request):
            seen.append(request)
            self.assertEqual(request.method, "GET")
            self.assertNotIn("cookie", request.headers)
            if request.url.host == "jwmis.ncwu.edu.cn":
                return httpx.Response(302, headers={"location": "https://example.org/"})
            self.assertEqual(request.url.host, "authserver.ncwu.edu.cn")
            return httpx.Response(200, text="<html>login</html>")

        report = await probe_official_login_surfaces(transport=httpx.MockTransport(handler))
        self.assertEqual(len(seen), 2)
        self.assertEqual([r.state for r in report], [
            ProbeState.REACHABLE, ProbeState.REACHABLE, ProbeState.APP_ONLY
        ])
        self.assertEqual([r.http_status for r in report], [302, 200, None])
        self.assertIsNotNone(report[0].elapsed_ms)

    async def test_restrictive_response_halts_other_web_probes(self):
        seen = []

        def handler(request):
            seen.append(request.url.host)
            return httpx.Response(429)

        report = await probe_official_login_surfaces(transport=httpx.MockTransport(handler))
        self.assertEqual(seen, ["jwmis.ncwu.edu.cn"])
        self.assertEqual([r.state for r in report], [
            ProbeState.RATE_LIMITED, ProbeState.SKIPPED, ProbeState.APP_ONLY
        ])

    async def test_login_reachability_is_not_catalog_verification(self):
        def handler(request):
            return httpx.Response(200, text="authentication page")

        report = await probe_official_login_surfaces(transport=httpx.MockTransport(handler))
        self.assertEqual(report[0].state, ProbeState.REACHABLE)
        # Reachability records cannot be substituted for authenticated catalog evidence.
        with self.assertRaises(TypeError):
            choose_readonly_catalog_route(report, account_scope="accountA", now=100)


class SelectionGateTest(unittest.TestCase):
    NOW = 1000.0

    def sample(self, entry_id="ncwu_web", status=CatalogState.VERIFIED_OK,
               account="A", age=1, proven=True, ms=50):
        return CatalogObservation(
            entry_id, account, status, self.NOW - age,
            identity_verified=proven, catalog_verified=proven, latency_ms=ms
        )

    def test_no_proof_no_route_even_if_login_works(self):
        with self.assertRaises(ValueError):
            self.sample(proven=False)
        self.assertIsNone(choose_readonly_catalog_route([], account_scope="A", now=self.NOW))

    def test_only_fresh_same_account_verified_catalog_can_route(self):
        self.assertEqual(
            choose_readonly_catalog_route([self.sample()],
                account_scope="A", now=self.NOW),
            "ncwu_web"
        )
        self.assertIsNone(choose_readonly_catalog_route(
            [self.sample()], account_scope="B", now=self.NOW
        ))
        self.assertIsNone(choose_readonly_catalog_route(
            [self.sample(age=300)], account_scope="A", now=self.NOW
        ))

    def test_ssobrowser_and_mobile_never_selected_as_catalog(self):
        observations = [self.sample(entry_id="ncwu_sso", ms=1),
                        self.sample(entry_id="ncwu_xiqueer", ms=1)]
        self.assertIsNone(choose_readonly_catalog_route(
            observations, account_scope="A", now=self.NOW
        ))

    def test_auth_failure_or_rate_limit_does_not_trigger_fallback(self):
        # Synthetic pre-approved second WEB_DIRECT route: not present in real registry.
        sandbox_entries = OFFICIAL_ENTRIES + (
            OfficialEntry("fixture_second", "Synthetic test route",
                          EntryKind.WEB_DIRECT, "https://example.edu/", True),
        )
        good = self.sample(entry_id="fixture_second", ms=40)
        expired = self.sample(status=CatalogState.AUTH_EXPIRED, proven=False)
        limited = self.sample(status=CatalogState.RATE_LIMITED, proven=False)
        for bad in (expired, limited):
            self.assertIsNone(choose_readonly_catalog_route(
                [bad, good], account_scope="A", current_entry_id="ncwu_web",
                now=self.NOW, trusted_registry=sandbox_entries
            ))

    def test_unavailable_route_can_choose_another_only_with_verified_catalog(self):
        sandbox_entries = OFFICIAL_ENTRIES + (
            OfficialEntry("fixture_second", "Synthetic test route",
                          EntryKind.WEB_DIRECT, "https://example.edu/", True),
        )
        samples = [
            self.sample(status=CatalogState.UNAVAILABLE, proven=False),
            self.sample(entry_id="fixture_second", ms=80)
        ]
        self.assertEqual(choose_readonly_catalog_route(
            samples, account_scope="A", current_entry_id="ncwu_web",
            now=self.NOW, trusted_registry=sandbox_entries
        ), "fixture_second")
        self.assertIsNone(choose_readonly_catalog_route(
            samples, account_scope="A", current_entry_id="ncwu_web", now=self.NOW
        ))

    def test_keep_current_healthy_route_even_if_other_faster(self):
        sandbox_entries = OFFICIAL_ENTRIES + (
            OfficialEntry("fixture_second", "Synthetic test route",
                          EntryKind.WEB_DIRECT, "https://example.edu/", True),
        )
        samples = [self.sample(ms=90), self.sample(entry_id="fixture_second", ms=10)]
        self.assertEqual(choose_readonly_catalog_route(
            samples, account_scope="A", current_entry_id="ncwu_web",
            now=self.NOW, trusted_registry=sandbox_entries
        ), "ncwu_web")


if __name__ == "__main__":
    unittest.main()
