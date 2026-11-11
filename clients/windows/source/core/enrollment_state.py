"""Guarded, deterministic offline state machine for one enrollment target."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class EngineState(str, Enum):
    INITIALIZING = "INITIALIZING"
    PREFLIGHT = "PREFLIGHT"
    MONITORING = "MONITORING"
    TRIGGERED = "TRIGGERED"
    SUBMITTING = "SUBMITTING"
    VERIFYING = "VERIFYING"
    VERIFY_WAIT = "VERIFY_WAIT"
    RETRY_WAIT = "RETRY_WAIT"
    SUCCESS = "SUCCESS"
    SKIPPED = "SKIPPED"
    PROTECTED = "PROTECTED"


ALLOWED_TRANSITIONS: dict[EngineState, frozenset[EngineState]] = {
    EngineState.INITIALIZING: frozenset({EngineState.PREFLIGHT}),
    EngineState.PREFLIGHT: frozenset({EngineState.MONITORING, EngineState.PROTECTED}),
    EngineState.MONITORING: frozenset({
        EngineState.TRIGGERED, EngineState.SUCCESS, EngineState.SKIPPED,
        EngineState.VERIFY_WAIT, EngineState.PROTECTED,
    }),
    EngineState.TRIGGERED: frozenset({
        EngineState.SUBMITTING, EngineState.SKIPPED, EngineState.PROTECTED,
    }),
    EngineState.SUBMITTING: frozenset({EngineState.VERIFYING}),
    EngineState.VERIFYING: frozenset({
        EngineState.SUCCESS, EngineState.VERIFY_WAIT,
        EngineState.RETRY_WAIT, EngineState.PROTECTED,
    }),
    EngineState.VERIFY_WAIT: frozenset({
        EngineState.VERIFYING, EngineState.PROTECTED,
    }),
    EngineState.RETRY_WAIT: frozenset({EngineState.SKIPPED, EngineState.MONITORING}),
    EngineState.SUCCESS: frozenset(),
    EngineState.SKIPPED: frozenset(),
    EngineState.PROTECTED: frozenset(),
}


@dataclass(frozen=True, slots=True)
class Transition:
    before: EngineState
    after: EngineState
    reason: str


class InvalidTransition(ValueError):
    """A state move was not in the declared business transition table."""


class EnrollmentFSM:
    def __init__(self) -> None:
        self.state = EngineState.INITIALIZING
        self._history: list[Transition] = []

    @property
    def history(self) -> tuple[Transition, ...]:
        return tuple(self._history)

    def move(self, next_state: EngineState, reason: str) -> None:
        if (not isinstance(next_state, EngineState)
                or next_state not in ALLOWED_TRANSITIONS[self.state]):
            raise InvalidTransition(f"非法状态转换：{self.state} -> {next_state}")
        if (not isinstance(reason, str) or not 1 <= len(reason) <= 64
                or any(ord(c) < 32 for c in reason)):
            raise ValueError("状态原因码无效")
        self._history.append(Transition(self.state, next_state, reason))
        self.state = next_state
