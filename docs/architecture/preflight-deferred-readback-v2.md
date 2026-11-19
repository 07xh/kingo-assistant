# V2: prepared preflight and deferred official readback

Status: **offline-implemented, not evidence of an approved NCWU write adapter**.
This design does not bypass the official opening window, access policy or
request-frequency constraints. The Windows application must keep the
production submission control disabled until school-specific end-to-end
acceptance has passed.

## Phase 1: optional pre-window timetable preparation

- The scheduler can request one read-only timetable snapshot in the final
  30 seconds before the configured selection time.
- This happens **only** if the real school gateway explicitly reports
  `preopen_schedule_permitted=True` and exposes a session-bound
  `preflight_session_token()`. Missing permissions mean **no extra request**.
- The snapshot is in RAM only and usable for a maximum of 30 seconds with
  precisely the same gateway identity, semester, session and ordered targets.
  Any mismatch automatically triggers the normal fresh read.
- **Never cached:** official opening-window result, exact existing selections,
  live course offerings/remaining seats, one-time write tokens and authoritative
  readback. All remain checked by the existing state machine.
- This saves a timetable read on the execution path only when the source's
  permission and freshness contract permit it. It is not a universal
  guarantee of faster submission.

## Phase 2: terminal-target readback coordinator

- The Windows scheduled worker opts into `defer_readback=True` through the
  scheduler. Direct `EnrollmentEngine.run()` callers retain synchronous
  verification unless explicitly opted in.
- **Only the final target** can transfer to `VerificationCoordinator`. Earlier
  targets keep synchronous official checks because ordered priority/fallback
  selection may depend on their actual result.
- After at most one submitted POST with a durable pending journal intent, the
  scheduler returns a provisional result. The UI shows pending, **not success**.
- The coordinator performs bounded read-only exact official class+course
  verification with the same original session and shared request policy,
  persists confirmed/rejected only when justified, and never submits a POST.
- The desktop worker waits for that coordinator before closing the gateway;
  quitting cooperatively does not interrupt the in-flight readback.
- Network failure, ambiguous receipt, late official result or an interrupted
  process leave the journal in `pending`. A subsequent session must use the
  existing read-only reconciliation, **not an automatic resend**.
- This deliberately does not launch multiple simultaneous POSTs or speculate
  across dependent teaching-class alternatives.

## Acceptance and performance

Synthetic tests cover permitted vs unpermitted prefetch, session change,
fresh readback, failed verification and rejected submissions. Windows CI must
pass including packaged GUI smoke. Live performance is not yet measurable.

Separately record the following once an approved real adapter exists:
`pre_submit_latency`, `budget_wait`, `submit_round_trip`,
`verification_latency`, and any duplicated HTTP writes (must be zero).
