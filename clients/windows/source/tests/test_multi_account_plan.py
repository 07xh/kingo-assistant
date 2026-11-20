"""Offline tests for multi-account plan fan-out.

Two identities must never share a budget database (they would serialise each
other) and must never target the same class (duplicate booking that the journal
cannot deduplicate across accounts). Both are config errors caught before any
socket opens.
"""
import unittest
from pathlib import Path

from core.multi_account_plan import (
    AccountTarget, PlanError, build_account_plans, summarize,
)

BASE = "https://jwmis.ncwu.edu.cn"


def make_config(**overrides) -> dict:
    config = {
        "base_url": BASE,
        "server_policy": {
            "verified": True,
            "source": "校级选课系统访问规范 2026-11",
            "origin": BASE,
            "windows": [[60, 2], [3600, 40]],
            "database": "D:/kingo/budget/shared.sqlite",
        },
        "accounts": [
            {
                "name": "A",
                "database": "D:/kingo/budget/A.sqlite",
                "proxy": "http://127.0.0.1:7890",
                "targets": [{"key": "a1", "course_id": "K001", "class_id": "C1"}],
            },
            {
                "name": "B",
                "database": "D:/kingo/budget/B.sqlite",
                "proxy": "socks5h://127.0.0.1:1080",
                "targets": [{"key": "b1", "course_id": "K002", "class_id": "C2"}],
            },
        ],
    }
    config.update(overrides)
    return config


class HappyPathTests(unittest.TestCase):
    def test_two_accounts_produce_two_plans(self):
        plans = build_account_plans(make_config())
        self.assertEqual([p.name for p in plans], ["A", "B"])
        self.assertEqual([str(p.database) for p in plans],
                         [str(Path("D:/kingo/budget/A.sqlite")),
                          str(Path("D:/kingo/budget/B.sqlite"))])
        self.assertEqual([p.egress for p in plans],
                         ["http://127.0.0.1:7890", "socks5h://127.0.0.1:1080"])

    def test_shared_windows_and_origin_are_inherited(self):
        plans = build_account_plans(make_config())
        for plan in plans:
            self.assertEqual(plan.origin, BASE)
            self.assertEqual(plan.windows, ((60.0, 2), (3600.0, 40)))
            self.assertEqual(plan.policy_source, "校级选课系统访问规范 2026-11")
            self.assertEqual(plan.max_concurrency, 1)

    def test_single_account_without_accounts_section(self):
        config = make_config()
        config.pop("accounts")
        config.update({
            "database": "D:/kingo/budget/only.sqlite",
            "targets": [{"course_id": "K001", "class_id": "C1"}],
        })
        plans = build_account_plans(config)
        self.assertEqual(len(plans), 1)
        self.assertEqual(plans[0].name, "account1")

    def test_summary_counts_distinct_resources(self):
        summary = summarize(build_account_plans(make_config()))
        self.assertEqual(summary["accounts"], 2)
        self.assertEqual(summary["targets"], 2)
        self.assertEqual(summary["distinct_databases"], 2)
        self.assertEqual(summary["distinct_egress"], 2)

    def test_summary_is_credential_free(self):
        summary = summarize(build_account_plans(make_config()))
        blob = str(summary)
        self.assertNotIn("password", blob.lower())
        self.assertNotIn("cookie", blob.lower())


class DatabaseConflictTests(unittest.TestCase):
    def test_same_database_rejected(self):
        config = make_config()
        config["accounts"][1]["database"] = "D:/kingo/budget/A.sqlite"
        with self.assertRaises(PlanError):
            build_account_plans(config)

    def test_case_insensitive_database_collision_rejected(self):
        config = make_config()
        config["accounts"][1]["database"] = "d:/KINGO/budget/a.SQLITE"
        with self.assertRaises(PlanError):
            build_account_plans(config)

    def test_relative_database_rejected(self):
        config = make_config()
        config["accounts"][0]["database"] = "budget/A.sqlite"
        with self.assertRaises(PlanError):
            build_account_plans(config)

    def test_memory_database_rejected(self):
        config = make_config()
        config["accounts"][0]["database"] = ":memory:"
        with self.assertRaises(PlanError):
            build_account_plans(config)

    def test_missing_database_rejected(self):
        config = make_config()
        config["server_policy"].pop("database")
        config["accounts"][0].pop("database")
        with self.assertRaises(PlanError):
            build_account_plans(config)


class TargetConflictTests(unittest.TestCase):
    def test_same_target_in_two_accounts_rejected(self):
        config = make_config()
        config["accounts"][1]["targets"] = [
            {"course_id": "K001", "class_id": "C1"}]
        with self.assertRaises(PlanError):
            build_account_plans(config)

    def test_same_target_with_whitespace_rejected(self):
        config = make_config()
        config["accounts"][1]["targets"] = [
            {"course_id": " K001 ", "class_id": "C1 "}]
        with self.assertRaises(PlanError):
            build_account_plans(config)

    def test_missing_class_id_rejected(self):
        config = make_config()
        config["accounts"][0]["targets"] = [{"course_id": "K001"}]
        with self.assertRaises(PlanError):
            build_account_plans(config)

    def test_empty_targets_rejected(self):
        config = make_config()
        config["accounts"][0]["targets"] = []
        with self.assertRaises(PlanError):
            build_account_plans(config)


class ProxyTests(unittest.TestCase):
    def test_same_proxy_on_two_accounts_rejected(self):
        config = make_config()
        config["accounts"][1]["proxy"] = "http://127.0.0.1:7890"
        with self.assertRaises(PlanError):
            build_account_plans(config)

    def test_unsupported_scheme_rejected(self):
        for scheme in ("ftp://host:21", "127.0.0.1:7890", "file:///tmp/x"):
            with self.subTest(scheme=scheme):
                config = make_config()
                config["accounts"][0]["proxy"] = scheme
                with self.assertRaises(PlanError):
                    build_account_plans(config)

    def test_empty_proxy_means_os_default(self):
        config = make_config()
        config["accounts"][0].pop("proxy")
        plans = build_account_plans(config)
        self.assertEqual(plans[0].egress, "os-default")


class OriginTests(unittest.TestCase):
    def test_origin_must_match_shared_policy(self):
        config = make_config()
        config["accounts"][0]["base_url"] = "https://other.example.edu.cn"
        with self.assertRaises(PlanError):
            build_account_plans(config)

    def test_trailing_slash_is_normalised(self):
        config = make_config()
        config["accounts"][0]["base_url"] = BASE + "/"
        plans = build_account_plans(config)
        self.assertEqual(plans[0].origin, BASE)

    def test_http_origin_rejected(self):
        config = make_config(base_url="http://jwmis.ncwu.edu.cn")
        config["server_policy"]["origin"] = "http://jwmis.ncwu.edu.cn"
        for account in config["accounts"]:
            account.pop("base_url", None)
        with self.assertRaises(PlanError):
            build_account_plans(config)

    def test_missing_origin_rejected(self):
        config = make_config()
        config.pop("base_url")
        config["server_policy"].pop("origin")
        with self.assertRaises(PlanError):
            build_account_plans(config)


class WindowTests(unittest.TestCase):
    def test_duplicate_window_lengths_rejected(self):
        config = make_config()
        config["accounts"][0]["windows"] = [[60, 2], [60, 5]]
        with self.assertRaises(PlanError):
            build_account_plans(config)

    def test_more_than_eight_windows_rejected(self):
        config = make_config()
        config["accounts"][0]["windows"] = [[i + 1, 1] for i in range(9)]
        with self.assertRaises(PlanError):
            build_account_plans(config)

    def test_non_integer_count_rejected(self):
        config = make_config()
        config["accounts"][0]["windows"] = [[60, 1.5]]
        with self.assertRaises(PlanError):
            build_account_plans(config)


class DataclassTests(unittest.TestCase):
    def test_target_repr_is_compact(self):
        target = AccountTarget(course_id="K001", class_id="C1")
        self.assertEqual(repr(target), "AccountTarget(K001/C1)")

    def test_target_rejects_control_characters(self):
        with self.assertRaises(PlanError):
            AccountTarget(course_id="K\x0001", class_id="C1")

    def test_duplicate_account_names_rejected(self):
        config = make_config()
        config["accounts"][1]["name"] = "A"
        with self.assertRaises(PlanError):
            build_account_plans(config)


if __name__ == "__main__":
    unittest.main()
