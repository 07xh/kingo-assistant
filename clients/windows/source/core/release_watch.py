"""Pure-logic core of the read-only watcher: matching, classification, dedup.

Extracted from ``core/scheduler.py`` so the decision rules can be tested without
httpx, tkinter or a school endpoint. ``Scheduler`` keeps the I/O and becomes a
thin wrapper over this module.

Semantics are deliberately identical to the code that used to live inline:

* a target may match zero, one or several catalog rows — several is ambiguous,
  never "pick the first";
* ``remain is None`` is UNKNOWN, never "available";
* ``remain <= 0`` is FULL;
* a notification is armed when the (course, class) fingerprint changes, and
  rearmed whenever the row is no longer available, so a seat that frees up again
  produces a second alert.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Iterable, Mapping, Sequence

#: Fields compared between a configured target and a catalog row. Exact match
#: for identifiers, case-insensitive containment for human-readable text.
_EXACT_FIELDS = ("course_id", "class_id")
_TEXT_FIELDS = ("course_name", "teacher")


class WatchState(str, Enum):
    AVAILABLE = "AVAILABLE"
    FULL = "FULL"
    UNKNOWN = "UNKNOWN"
    MISSING = "MISSING"
    AMBIGUOUS = "AMBIGUOUS"


@dataclass(frozen=True, slots=True)
class Observation:
    """One target's verdict for one catalog read."""

    key: str
    state: WatchState
    remain: int | None = None
    fingerprint: str | None = None
    item: Any = None

    @property
    def notifiable(self) -> bool:
        return self.state is WatchState.AVAILABLE and self.fingerprint is not None


def target_matches(target: Mapping[str, Any], item: Any) -> bool:
    """Replicates the original ``Scheduler.matches`` predicate exactly."""
    for name in _EXACT_FIELDS:
        needle = target.get(name)
        if not needle:
            continue
        value = getattr(item, name, None)
        if value is None:
            return False
        if str(needle).strip() != str(value).strip():
            return False
    for name in _TEXT_FIELDS:
        needle = target.get(name)
        if not needle:
            continue
        value = getattr(item, name, None)
        if value is None:
            return False
        if str(needle).strip().casefold() not in str(value).casefold():
            return False
    return True


def observe(target: Mapping[str, Any], catalog: Sequence[Any], *,
            fingerprint_of: Any = None) -> Observation:
    """Classify one target. ``fingerprint_of`` lets the caller reuse its hasher."""
    key = str(target.get("key", ""))
    matching = [row for row in catalog if target_matches(target, row)]
    if len(matching) > 1:
        return Observation(key, WatchState.AMBIGUOUS)
    if not matching:
        return Observation(key, WatchState.MISSING)
    item = matching[0]
    remain = getattr(item, "remain", None)
    if remain is None:
        return Observation(key, WatchState.UNKNOWN, item=item)
    if remain <= 0:
        return Observation(key, WatchState.FULL, remain=remain, item=item)
    course_id = getattr(item, "course_id", "")
    class_id = getattr(item, "class_id", None)
    fingerprint = (fingerprint_of(course_id, class_id) if fingerprint_of is not None
                   else _default_fingerprint(course_id, class_id))
    return Observation(key, WatchState.AVAILABLE, remain=remain,
                       fingerprint=fingerprint, item=item)


def _default_fingerprint(course_id: str, class_id: str | None) -> str:
    import hashlib
    import json
    blob = json.dumps([course_id, class_id], ensure_ascii=True,
                      separators=(",", ":"))
    return hashlib.sha256(blob.encode("ascii")).hexdigest()


def observe_all(targets: Iterable[Mapping[str, Any]], catalog: Sequence[Any], *,
                fingerprint_of: Any = None) -> list[Observation]:
    return [observe(target, catalog, fingerprint_of=fingerprint_of)
            for target in targets]


class NotificationLedger:
    """Delivered-alert fingerprints, keyed by target key.

    Wraps the caller's dict in place so existing save/load paths and the
    previous attribute name keep working unchanged.
    """

    def __init__(self, remembered: dict[str, str] | None = None) -> None:
        self.active: dict[str, str] = remembered if remembered is not None else {}

    def rearm(self, key: str) -> None:
        """Forget the last delivered fingerprint so the next availability alerts."""
        self.active.pop(key, None)

    def should_notify(self, key: str, fingerprint: str | None) -> bool:
        if not fingerprint:
            return False
        return self.active.get(key) != fingerprint

    def mark(self, key: str, fingerprint: str | None) -> None:
        if fingerprint:
            self.active[key] = fingerprint

    def snapshot(self) -> dict[str, str]:
        return dict(self.active)

    def __len__(self) -> int:
        return len(self.active)


def apply_observations(targets: Sequence[Mapping[str, Any]],
                       observations: Sequence[Observation],
                       ledger: NotificationLedger) -> list[tuple]:
    """Fold observations into the ledger and return the alerts to deliver.

    Returns ``(key, fingerprint, target, item)`` tuples in target order. The
    ledger is only rearmed here; marking happens after the caller confirms
    delivery, so a failed push does not consume the alert.
    """
    by_key = {obs.key: obs for obs in observations}
    pending: list[tuple] = []
    for target in targets:
        key = str(target.get("key", ""))
        obs = by_key.get(key)
        if obs is None:
            continue
        if obs.notifiable:
            if ledger.should_notify(key, obs.fingerprint):
                pending.append((key, obs.fingerprint, target, obs.item))
        else:
            ledger.rearm(key)
    return pending
