# AGENTS.md — KingoAssistant Engineering Contract

This file is the repository-wide contract for human and AI contributors. Read it before changing code, configuration, tests, workflows, or release artifacts.

## 1. Repository truth comes before assumptions

The current `main` baseline is a Windows Python read-only course monitor. The real NCWU elective catalog and remaining-seat semantics are not verified. The current repository does not contain a live NCWU enrollment-write adapter. `clients/windows/source/core/enrollment.py` is an offline-testable orchestration core that uses an injected fake/verified gateway contract; its presence is not proof that school submission works.

The Android client is planned separately and has no released APK.

Before every task:
- Inspect the current branch, working tree, recent commits, relevant PRs, and CI.
- Read the implementation and tests that already own the behavior.
- Treat README, architecture docs, fixtures, tests, and workflow results as evidence; do not infer runtime capability from a branch name, class name, mock, or successful package build.
- Do not duplicate existing modules. Extend or refactor the current implementation in `clients/windows/source/` unless a documented architecture decision justifies a new location.

## 2. Authorization and request discipline

- Use only the user's own account and officially permitted access paths.
- Never bypass CAPTCHA, authentication, access controls, WAF controls, or request limits. Never disguise traffic or create parallel request bursts.
- User consent to run a faster setting is not institutional authorization. A benchmark, theoretical QPS estimate, or successful offline test is not evidence of an allowed school request rate.
- Keep the current conservative default. Any interval below 60 seconds is disabled unless a verifiable written institutional policy explicitly authorizes that host, read-only operation, and minimum interval. A GUI checkbox alone must never enable it.
- Serialize requests for one account. Apply pacing at the transport/request layer, not only between scheduler cycles. Do not perform catch-up bursts after a delay.
- Stop on 401/403/429, authentication redirects, unexpected challenge pages, or schema uncertainty. Do not automatically retry a 403/429. If a server supplies `Retry-After`, do not issue another request before that deadline; surface the cooldown to the user.
- Retry only idempotent, explicitly read-only operations, with bounded attempts and backoff. Respect `Retry-After` on applicable transient responses such as 503.
- Keep read-only transport separate from any future write adapter. Do not send enrollment writes through `KingoClient.read`.

## 3. Enrollment safety invariants

- The current enrollment engine remains offline-only until an official, permitted write contract is independently verified and a dedicated adapter is reviewed.
- User opt-in, institutional permission, and a verified adapter are three independent gates. All are required; none substitutes for another.
- Match the complete target identity: semester, course ID, and exact teaching-class ID. Names and teacher labels are additional consistency checks, not substitutes for IDs.
- Validate known prerequisites before a write: current enrollment, exact offering, confirmed positive remaining capacity, schedule conflicts, and any documented eligibility rules. Unknown or missing data blocks automatic execution.
- Reserve a durable attempt/idempotency fence before any future write.
- Never blindly retry a write after timeout, disconnect, 5xx, or any ambiguous result. Enter verification and query the authoritative read-only enrollment record.
- Allow a bounded read-only verification window for eventual consistency. If the result remains unknown, persist the ambiguous state and stop; do not submit again.
- HTTP 200, a success toast, or an accepted transport response is not proof of enrollment. Only the authoritative official enrollment record can confirm success.
- A fallback choice is allowed only after the previous attempt has an explicit official rejection and a fresh read confirms the enrollment state. Never fall back after an ambiguous result.

## 4. Existing module ownership

Preserve these boundaries unless a reviewed migration changes them:

- `core/client.py`: authenticated, scoped, read-only HTTP transport and parsing. It is not an enrollment-write client.
- `core/scheduler.py`: read-only monitoring and notification scheduling.
- `core/monitor_policy.py`: polling interval and duration policy. Reuse it; do not create a second competing rate-limit configuration.
- `core/preflight.py`: local configuration/readiness checks only. A local READY result is not proof of live school access.
- `core/conflicts.py`: conservative conflict detection for manually entered timetable hints. It is not yet a complete official timetable engine.
- `core/monitor_state.py`: notification de-duplication persistence.
- `core/enrollment.py`: offline-testable orchestration and attempt journal. It has no live NCWU adapter.
- `tests/`: offline and synthetic fixtures. Never add raw HAR, cookies, tokens, student records, or private course data.

Do not create a second root-level `core/` or `models/` tree that duplicates `clients/windows/source/core/`.

## 5. Domain models and state transitions

- Prefer typed domain models with explicit validation. Frozen dataclasses should use immutable nested values (for example, tuples rather than mutable lists).
- Keep target identity separate from live observations and ephemeral credentials. Never persist dynamic tokens in course models, config files, logs, or fixtures.
- Represent timetable slots with day, period range, week range/parity, and any known exceptions. If week or adjustment information is missing, report conflict status as unknown rather than assuming no conflict.
- Use an explicit state enum and a reviewed transition table for the enrollment workflow. Reject illegal transitions. Record redacted transition events for diagnosis.
- Keep parser functions pure and fail closed on unexpected page structure, ambiguous matching, or missing critical fields.

## 6. Secrets, privacy, and diagnostics

- Never commit passwords, cookies, session IDs, authorization headers, signatures, notification tokens, raw HAR files, or personal academic records.
- Prefer OS-backed secret storage or process environment variables. Do not introduce plaintext credential storage in YAML or source code.
- Redact URLs with sensitive query parameters, headers, response bodies, and user/course details from logs and exception messages.
- Public fixtures must be synthetic or demonstrably sanitized. Do not assume hashing alone anonymizes identifiers.

## 7. Required tests and release gates

Every behavior change must include relevant offline tests. At minimum, cover:
- exact course/class/semester matching and ambiguous records;
- parser schema drift and missing fields;
- time overlap, week parity, and unknown timetable data;
- every legal and illegal FSM transition;
- timeout/disconnect/5xx ambiguity and no duplicate writes;
- delayed read-only verification and restart recovery from the attempt journal;
- 401/403/429 stop behavior and `Retry-After` handling;
- configuration defaults, secret redaction, and Windows GUI persistence.

Use synthetic responses and mock transports. Tests must not contact a real university.

Track each capability honestly through these states:
`DESIGNED` → `IMPLEMENTED` → `UNIT_TESTED` → `INTEGRATION_TESTED` → `LIVE_VERIFIED` → `MERGED` → `DEPLOYED`.

A Windows build, mock test, offline fixture, or local configuration check cannot be described as live NCWU verification. Never claim a later gate without its evidence.
