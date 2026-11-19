"""Offline tests for the extracted watcher decision rules.

Pure logic: no httpx, no scheduler thread, no school endpoint. Every case here
mirrors a branch that used to live inline in ``Scheduler.cycle``.
"""
import unittest
from dataclasses import dataclass

from core.release_watch import (
    NotificationLedger,
    Observation,
    WatchState,
    apply_observations,
    observe,
    observe_all,
    target_matches,
)


@dataclass
class Row:
    """Stand-in for core.client.CourseItem."""
    course_id: str
    course_name: str = ""
    teacher: str = ""
    remain: int | None = 0
    class_id: str | None = None


def target(**overrides):
    base = {"key": "A", "course_id": "C1"}
    base.update(overrides)
    return base


class MatchTests(unittest.TestCase):
    def test_empty_target_matches_everything(self):
        self.assertTrue(target_matches({}, Row("C1")))

    def test_course_id_is_exact_after_strip(self):
        self.assertTrue(target_matches(target(course_id=" C1 "), Row("C1")))
        self.assertFalse(target_matches(target(course_id="C1"), Row("C10")))

    def test_missing_attribute_fails_closed(self):
        class Bare:
            course_id = "C1"
        self.assertFalse(target_matches(target(course_name="高数"), Bare()))

    def test_text_fields_are_case_insensitive_containment(self):
        row = Row("C1", course_name="高等数学 A", teacher="张老师")
        self.assertTrue(target_matches(target(course_name="高等数学"), row))
        self.assertTrue(target_matches(target(course_name="高等数学 a"), row))
        self.assertFalse(target_matches(target(course_name="线性代数"), row))

    def test_class_id_must_match_exactly_when_given(self):
        self.assertTrue(target_matches(target(class_id="K1"), Row("C1", class_id="K1")))
        self.assertFalse(target_matches(target(class_id="K2"), Row("C1", class_id="K1")))


class ObserveTests(unittest.TestCase):
    def test_multiple_rows_is_ambiguous_never_first_wins(self):
        catalog = [Row("C1", remain=5), Row("C1", remain=5, class_id="K2")]
        obs = observe(target(), catalog)
        self.assertEqual(obs.state, WatchState.AMBIGUOUS)
        self.assertIsNone(obs.item)

    def test_absent_row_is_missing(self):
        self.assertEqual(observe(target(), [Row("C9")]).state, WatchState.MISSING)

    def test_unknown_remain_is_not_available(self):
        obs = observe(target(), [Row("C1", remain=None)])
        self.assertEqual(obs.state, WatchState.UNKNOWN)
        self.assertIsNone(obs.fingerprint)
        self.assertFalse(obs.notifiable)

    def test_zero_and_negative_remain_are_full(self):
        for remain in (0, -1):
            with self.subTest(remain=remain):
                self.assertEqual(observe(target(), [Row("C1", remain=remain)]).state,
                                 WatchState.FULL)

    def test_available_carries_fingerprint_and_remain(self):
        obs = observe(target(), [Row("C1", remain=3, class_id="K1")])
        self.assertEqual(obs.state, WatchState.AVAILABLE)
        self.assertEqual(obs.remain, 3)
        self.assertTrue(obs.notifiable)
        self.assertEqual(len(obs.fingerprint), 64)

    def test_injected_fingerprint_function_is_used(self):
        obs = observe(target(), [Row("C1", remain=1)],
                      fingerprint_of=lambda course, klass: "custom")
        self.assertEqual(obs.fingerprint, "custom")

    def test_observe_all_preserves_target_order(self):
        targets = [target(key="B", course_id="C2"), target(key="A", course_id="C1")]
        obs = observe_all(targets, [Row("C1", remain=1)])
        self.assertEqual([o.key for o in obs], ["B", "A"])


class LedgerTests(unittest.TestCase):
    def test_first_availability_notifies(self):
        ledger = NotificationLedger({})
        self.assertTrue(ledger.should_notify("A", "f1"))

    def test_same_fingerprint_does_not_repeat(self):
        ledger = NotificationLedger({})
        ledger.mark("A", "f1")
        self.assertFalse(ledger.should_notify("A", "f1"))

    def test_different_fingerprint_notifies_again(self):
        ledger = NotificationLedger({"A": "f1"})
        self.assertTrue(ledger.should_notify("A", "f2"))

    def test_rearm_allows_a_repeat(self):
        ledger = NotificationLedger({"A": "f1"})
        ledger.rearm("A")
        self.assertTrue(ledger.should_notify("A", "f1"))

    def test_missing_fingerprint_never_notifies(self):
        ledger = NotificationLedger({})
        self.assertFalse(ledger.should_notify("A", None))
        ledger.mark("A", None)
        self.assertEqual(len(ledger), 0)


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.targets = [target(key="A", course_id="C1")]

    def test_available_returns_pending_alert(self):
        ledger = NotificationLedger({})
        obs = observe_all(self.targets, [Row("C1", remain=2)])
        pending = apply_observations(self.targets, obs, ledger)
        self.assertEqual(len(pending), 1)
        key, fingerprint, row_target, item = pending[0]
        self.assertEqual(key, "A")
        self.assertEqual(row_target["course_id"], "C1")
        self.assertEqual(item.remain, 2)
        self.assertEqual(ledger.active, {})   # marking is the caller's job

    def test_full_rearms_so_a_later_release_alerts_again(self):
        ledger = NotificationLedger({"A": "old"})
        obs = observe_all(self.targets, [Row("C1", remain=0)])
        self.assertEqual(apply_observations(self.targets, obs, ledger), [])
        self.assertNotIn("A", ledger.active)

        released = observe_all(self.targets, [Row("C1", remain=1)])
        self.assertEqual(len(apply_observations(self.targets, released, ledger)), 1)

    def test_already_delivered_fingerprint_is_suppressed(self):
        ledger = NotificationLedger({})
        first = observe_all(self.targets, [Row("C1", remain=2, class_id="K1")])
        pending = apply_observations(self.targets, first, ledger)
        ledger.mark(pending[0][0], pending[0][1])
        again = observe_all(self.targets, [Row("C1", remain=2, class_id="K1")])
        self.assertEqual(apply_observations(self.targets, again, ledger), [])

    def test_unknown_state_rearms_without_notifying(self):
        ledger = NotificationLedger({"A": "old"})
        obs = observe_all(self.targets, [Row("C1", remain=None)])
        self.assertEqual(apply_observations(self.targets, obs, ledger), [])
        self.assertNotIn("A", ledger.active)

    def test_observation_for_unknown_target_is_ignored(self):
        ledger = NotificationLedger({})
        orphan = [Observation("Z", WatchState.AVAILABLE, remain=1, fingerprint="f")]
        self.assertEqual(apply_observations(self.targets, orphan, ledger), [])

    def test_shared_dict_object_keeps_legacy_attribute_alias_working(self):
        shared = {"A": "old"}
        ledger = NotificationLedger(shared)
        self.assertIs(ledger.active, shared)
        ledger.mark("A", "new")
        self.assertEqual(shared["A"], "new")


if __name__ == "__main__":
    unittest.main()
