# KingoAssistant V5.1 → V2 offline FSM integration

**Status:** implementation under review. No NCWU live enrollment adapter or permitted write contract is implemented.

## Reuse rather than duplicate

- `core/enrollment.py` remains the only public `EnrollmentEngine` API and retains the V5.1 capability gates: verified catalog, enrolled records, official selection window, verified write contract, user opt-in, institutional permission, and account scope.
- The existing HMAC-SHA256 journal with a per-file random salt, V1 migration, OS process lock, and persisted `pending/rejected/confirmed` outcomes remains unchanged. Historical pending attempts cannot be silently replayed.
- The stateful implementation is factored into `core/enrollment_flow.py` and called by `EnrollmentEngine.run`. It is not a second engine.
- `core/course_models.py` defines immutable target, offering and week-aware timetable contracts. Critical missing school data is unknown, not inferred.
- `core/enrollment_state.py` contains a closed FSM transition table and typed redacted transition history.
- `core/preflight.py` adds `inspect_enrollment_schedule` for trusted term-checked timetable data; its existing manual timetable warnings remain informational.

## Key transitions

```text
INITIALIZING -> PREFLIGHT -> MONITORING -> TRIGGERED -> SUBMITTING
                 |                              |            |
                 +-> PROTECTED                  +-> SKIPPED  +-> VERIFYING
                                                                  |
                              +-> SUCCESS <-----------------------+
                              |                                   |
                              +-> VERIFY_WAIT -> VERIFYING <------+
                              |
                              +-> PROTECTED  (unresolved after bounded reads)
                              |
                              +-> RETRY_WAIT -> SKIPPED (verified rejection only)
```

- No `VERIFY_WAIT` route can call `submit_once`; it only reads authoritative enrolled records.
- Both a timeout and an ambiguous acknowledgement enter `VERIFYING`.
- If a record is delayed, bounded read-only checks may confirm success; if the last result remains unknown, the pending HMAC journal entry remains in place.
- On restart, `pending` or `legacy_pending` entries are reconciled by read-only checks, never re-submitted.
- A known timetable conflict skips a target, while unknown week parity/schedule/term blocks automatic execution.
- V5.1's official selection window is checked before work and immediately before each attempted write; fallback refreshes the catalog and respects the configured priority order.

## Test and release gates

Offline tests enumerate every permitted and forbidden FSM edge and cover immutability, week parity, teacher/class/semester matching, timeout-after-commit, delayed read visibility, unresolved restart, legacy migration, strict journal/account isolation, window changes, duplicate prevention, and priority fallback. The GitHub Actions Windows workflow must pass tests, PyInstaller EXE build, packaged self-test and Tk GUI smoke.

Neither a local fake gateway nor green CI proves NCWU's real course directory, remaining-seat semantics, permission limits, or live write capability. The Windows user-facing client stays read-only.
