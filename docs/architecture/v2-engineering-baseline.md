# KingoAssistant V2 — Engineering Baseline and Migration Plan

**Date:** 2026-11-11  
**Scope:** Repository audit and architecture plan only. This document does not add a live school adapter or enable enrollment writes.

## 1. Audited repository snapshot

Audit target: `07xh/kingo-assistant`, default branch `main`, baseline commit `0152c35c31b318508bb0bb5ea7320d91de7ee286`.

Observed repository facts:

- Windows source lives in `clients/windows/source/`; the repository is not a blank project.
- The current product baseline is a Windows V4.6 read-only monitor. The README records 88 offline tests, a successful Windows-native EXE build, packaged self-test, and Tk GUI smoke test.
- The live NCWU elective catalog and the semantics of the remaining-seat field are explicitly **unverified**.
- No live NCWU enrollment-write adapter is present. `core/enrollment.py` is an offline-testable workflow using an injected gateway contract; the tests use a fake gateway.
- The Android client is planned separately; no APK is implemented or released.
- There is no root `AGENTS.md`, no root `models/` package, and no explicit enrollment FSM module today.

### GitHub / CI snapshot

| Item | Observed state |
|---|---|
| `main` | `0152c35`; latest main workflow run #34 succeeded (Actions run 42991429669) |
| PR #6 | Open Draft; latest head `7fd984f`; latest Windows workflow run #36 succeeded (Actions run 42992093483) |
| `feat/automatic-enrollment-engine-20261111` | Diverged from main (1 ahead / 2 behind); its offline engine is not a reason to merge the branch wholesale |
| `feat/windows-v5-integration` | Diverged from main (27 ahead / 3 behind); do not merge wholesale without a file-by-file review |

PR #6 adds per-request serialization/pacing and experimental 1.5/1/0.5-second read-only presets. Its own documentation correctly says these rates are not proven to be allowed by NCWU. However, the current opt-in path permits a user checkbox to enable a sub-60-second rate without a verified institutional permission record. **Keep PR #6 Draft until this is changed:** sub-60-second polling must require verifiable written permission scoped to the official host and read-only operation; user acknowledgement alone is insufficient. Passing CI does not resolve this policy gate.

## 2. Existing modules to preserve and extend

| Existing path | Current responsibility | V2 disposition |
|---|---|---|
| `core/client.py` | HTTP/1.1 AsyncClient, keep-alive pool, scoped URLs, GBK form preparation, HTML parsing, bounded read retries | Keep as read-only transport; add a single request-pacing boundary and explicit read-only endpoint/method allowlist |
| `core/scheduler.py` | One catalog read per monitoring cycle, local status and notification de-duplication | Keep as monitoring scheduler; do not turn it into the enrollment engine |
| `core/monitor_policy.py` | Interval, cycle-count and duration validation | Reuse as the single polling-policy owner; never add a competing rate config |
| `core/preflight.py` | Local-only readiness checks | Keep local-only and label results accordingly; it cannot prove live login or school permission |
| `core/conflicts.py` | Conservative overlap checks for manually entered day/period ranges | Extend only after week parity, week ranges, and timetable exceptions are modeled |
| `core/monitor_state.py` | Credential-free notification state persistence | Preserve independently from enrollment attempt records |
| `core/enrollment.py` | Offline sequential workflow, attempt journal, exact course/class match, fake gateway contract | Refactor in place into an explicit FSM; do not duplicate it |
| `tests/` | Synthetic fixtures and offline tests | Preserve offline-only behavior and add state, policy, and recovery coverage |

Do not create a second repository-root `core/` or `models/` tree. Keep Windows runtime code under `clients/windows/source/`.

## 3. Findings and gaps to close

### 3.1 Enrollment orchestration is not yet a formal FSM

The current `EnrollmentEngine` executes a sequential workflow and returns human-readable state strings. It has valuable safeguards—three independent readiness gates, exact course/class matching, an attempt journal written before submission, and a stop on ambiguous outcomes—but it has no explicit state enum, transition table, or transition-event record.

**Change:** introduce a typed state enum and guarded transition function inside the existing Windows source tree. Illegal transitions must fail closed and be unit-tested.

### 3.2 Domain models are too small for reliable prerequisite checks

Current `Target` and `Offering` primarily identify course and teaching class. They do not carry a complete term identity, display/teacher consistency fields, or structured timetable slots. The separate conflict helper handles only manually entered day/period ranges and does not model week parity or special adjustments.

**Change:** add typed domain models in the existing `core/` package (for example, `course_models.py` and `schedule_models.py`). Keep targets, live observations, and ephemeral server tokens separate. Use immutable nested values such as tuples. Missing critical identity or timetable data must produce an unknown/blocked result, not a guessed match.

### 3.3 Verification needs a bounded eventual-consistency phase

The current offline engine performs a read after a write and stops if the official record is not confirmed. That is safer than a blind retry, but it has no explicit bounded read-only verification window.

**Change:** add `VERIFY_WAIT` as a read-only state. It may repeat only the authoritative enrollment-record query at a policy-compliant interval for a bounded period. It must never repeat the write. If still unknown at the deadline, persist the ambiguous outcome and require manual reconciliation.

### 3.4 Read pacing belongs at the transport boundary

The mainline scheduler waits between cycles, while a cycle may perform more than one official request (for example, entry-page refresh and catalog read). The current client has an async client and a fetch lock, but the request method itself does not yet enforce one global minimum gap for every official read. PR #6 adds a request lock and pacing, which is directionally useful.

**Change:** centralize pacing around every official-host request, including identity, entry, catalog, retries, and any HTTP-Date fallback. Use one serialized account session. Keep the school-specific minimum interval policy in one place. No concurrent bursts, catch-up bursts, or rate-limit evasion.

### 3.5 Retry policy needs explicit server-directed cooldown handling

The mainline read client stops on 401/403/429 and retries selected transient failures with bounded backoff. It does not currently parse `Retry-After` for transient responses. The safest behavior for 403/429 remains stop-and-surface; for a response that supplies `Retry-After`, do not issue another request before the specified deadline. For 503 or other retryable read failures, server-directed delay must take precedence over local backoff.

**Change:** add a tested parser for delta-seconds and HTTP-date forms. Never retry a write. Keep read retries limited to idempotent, explicitly allowlisted read operations.

### 3.6 The offline attempt journal has a documentation mismatch

The current journal describes its fingerprints as salted, but the implementation computes a plain SHA-256 over semester/course/class identifiers without a secret salt. These identifiers are not credentials, but the description should be accurate.

**Change:** either correct the wording to “deterministic hash of course identifiers” or deliberately introduce a protected keyed construction only if a real privacy requirement justifies it. Do not claim that hashing anonymizes data.

### 3.7 The existing read-only boundary must remain intact

The main entry point requires `dry_run: true`, and the architecture docs state that no enrollment POST adapter exists. Do not weaken these checks as part of the FSM refactor. A future write adapter is a separate, gated milestone—not a side effect of making the offline engine more complete.

## 4. Compatible V2 target layout

Keep the current Windows source root and evolve it incrementally:

```text
clients/windows/source/
├── core/
│   ├── client.py                 # read-only HTTP transport; no enrollment writes
│   ├── scheduler.py              # read-only monitoring
│   ├── monitor_policy.py         # one request-frequency policy owner
│   ├── preflight.py              # local-only preflight
│   ├── conflicts.py              # schedule overlap rules
│   ├── enrollment.py             # refactored FSM orchestration
│   ├── course_models.py          # target / offering / observation contracts
│   ├── schedule_models.py        # week-aware timetable contracts
│   ├── enrollment_state.py       # state enum + guarded transition table
│   ├── verifier.py               # read-only enrollment-record verification
│   └── request_policy.py          # request pacing / Retry-After policy
├── adapters/
│   └── kingo/
│       ├── readonly.py            # only after live read protocol is verified
│       └── enrollment.py          # NOT IMPLEMENTED until official write permission and contract are verified
└── tests/
    ├── fixtures/                  # synthetic or sanitized, never raw HAR
    ├── test_enrollment.py
    ├── test_enrollment_state.py
    ├── test_verifier.py
    ├── test_request_policy.py
    └── ...
```

The adapter directories are a future boundary, not a claim that either adapter currently exists. Do not create a write adapter from guessed endpoints or from a HAR alone.

## 5. V2 state-machine contract

```text
INITIALIZING
    -> PREFLIGHT
    -> MONITORING
    -> TRIGGERED
    -> SUBMITTING
    -> VERIFYING
       -> SUCCESS
       -> VERIFY_WAIT -> VERIFYING
       -> PROTECTED (unknown after bounded verification)

PREFLIGHT -> PROTECTED (invalid session, missing permission, bad config)
TRIGGERED -> MONITORING (target mismatch, unknown data, conflict, no capacity)
SUBMITTING -> VERIFYING (accepted, timeout, disconnect, or ambiguous response)
SUBMITTING -> RETRY_WAIT (only explicit official rejection)
RETRY_WAIT -> MONITORING (only after allowed cooldown and a fresh read)
```

Invariants:

1. A write can occur only after independent user authorization, institutional permission, and a reviewed adapter capability all pass.
2. Before a write, persist an attempt fence for the exact semester/course/class identity.
3. Any uncertain write outcome goes to read-only verification; it never goes straight back to SUBMITTING.
4. A bounded verification timeout ends in PROTECTED/unknown, not a second write.
5. Only an authoritative official enrollment record confirms SUCCESS.
6. A fallback choice requires an explicit official rejection and a fresh read of the enrollment record.

## 6. Delivery phases

| Phase | Work | Exit evidence |
|---|---|---|
| 0. Engineering baseline | Root `AGENTS.md`, this migration plan, repository truth and safety invariants | Documentation PR reviewed; no runtime behavior changed |
| 1. Model and state contracts | Immutable typed course/term/timetable models; explicit state enum and transition guards | Unit tests for all legal/illegal transitions and exact identity matching |
| 2. Read transport hardening | One serialized request path; host scope; pacing; Retry-After; read-only method allowlist | MockTransport tests for every request path, 401/403/429, 503, timeout and cooldown |
| 3. Verification and journal | Bounded read-only verification, restart recovery, accurate journal documentation | Tests prove no duplicate write after timeout, restart, stale record, or delayed consistency |
| 4. Official adapter assessment | Verify school-published/authorized read and write contracts, if any; document host, scope, permitted operations and limits | Independent evidence and manual review; otherwise keep writes disabled |
| 5. Controlled integration | Implement only the verified adapter; keep it isolated from monitor client | Authorized integration test with no policy bypass and complete audit trail |
| 6. Windows release | GUI state display, secure credential handling, native build, packaged smoke tests | Latest CI green, release artifact verified, capability labels accurate |

## 7. Required capability labels

Use these exact labels in docs and UI:

- **设计中** — contract or plan only.
- **代码已实现** — source exists; not yet proven by tests.
- **离线测试通过** — synthetic tests pass.
- **集成测试通过** — components pass together against a controlled mock/test service.
- **真实只读验收通过** — the authorized live read-only behavior was manually verified against the official UI.
- **真实写入验收通过** — only after official write permission, adapter review, and authorized end-to-end verification.
- **已合并 / 已部署** — only with the corresponding GitHub merge or release evidence.

A green CI run proves only the jobs that ran. It does not verify NCWU endpoints, seat semantics, school policy, or successful enrollment.

## 8. Immediate next implementation order

1. Keep the V2 baseline documentation-only until reviewed.
2. Keep PR #6 Draft; replace checkbox-only fast polling authorization with a mandatory, verifiable institutional permission gate before any sub-60-second option can run.
3. Refactor the existing `core/enrollment.py` in place; do not create a second engine.
4. Add typed course and week-aware schedule contracts, then connect the existing conflict rules to preflight.
5. Add explicit FSM transitions and a bounded read-only verifier.
6. Harden request pacing and `Retry-After` handling without lowering the current conservative policy.
7. Reassess the live NCWU protocol only during an officially open window and through the user's own authorized official UI. Until the catalog and remaining-seat semantics are verified, keep live automation disabled.

**No runtime code, endpoint, credential, or enrollment behavior is changed by this plan.**
