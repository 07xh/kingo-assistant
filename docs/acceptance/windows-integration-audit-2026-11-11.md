# KingoAssistant Windows integration audit — 2026-11-11

Scope: source and Windows CI after PRs #19, #20, #21 and #22. This is an **engineering audit**, not acceptance of live NCWU enrollment.

## Execution paths

| Feature | Current implementation | What it actually does |
|---|---|---|
| Desktop monitor | `windows_gui.MonitorWorker` → `core.scheduler.Scheduler` → `KingoClient` | Authenticated, strictly read-only polling where a verified adapter is configured |
| Browser appointment | `toggle_portal_appointment` → UI timer → `webbrowser.open` | Manual fallback, **not** an automatic submission |
| Local plan admission | `inspect_scheduled_runner` → `prepare_scheduled_plan` | Offline validation, **does not initiate a scheduled enrollment run** |
| Automatic enrollment FSM | `ScheduledEnrollmentRunner` → `EnrollmentEngine` → `core.enrollment_flow` | Synthetic/mock adapter tests; **no verified NCWU writer exists** |
| Windows tray | `desktop_tray.TrayController` → Tk event queue | Keeps an already-running process alive when the window is hidden; not a service and not restart persistence |

## Findings and remedies

- **P0 / integrated GUI crash:** #20 originally referenced `portal_row`, deleted by the #19 UI refactor. The combined main branch was briefly not GUI-startable. The restored action is now attached to `action_panel`, and packaged GUI smoke asserts that it exists.
- **P1 / unnecessary local wakeups:** A distant booking time previously caused local timer wakeups twice per second. The scheduled runner now sleeps in cancelable coarse increments (30 s, then 5 s, then 250 ms during the final 10 seconds). This does **not** raise the number or rate of university network calls.
- **P1 / fake dependency in plan validation:** The plan preflight used `ScheduledEnrollmentRunner(None, TimeSync())` just to reuse constructor bounds checks. `validate_schedule_limits` now performs the same validation as a pure function reused by UI and actual runner.
- **P2 / repeated YAML loading:** #21 now uses a single in-memory configuration for preflight and startup while preserving live-mode validation.
- **P2 / redundant confirmation:** The explicit read-only-monitor start click is the opt-in; a second modal is no longer required.
- **P2 / advisory lesson schedule hints:** Invalid optional manually-entered time slots are advisory for *read-only* monitoring, not the authoritative academic timetable used to protect potential automatic writes.
- **P2 / flaky rate-limit test:** #22 checks the persisted Retry-After deadline instead of brittle elapsed wall-clock time.

## Genuine automatic-enrollment gates still open

1. Verify the student's authorized school-session flow, without publishing credentials, session cookies or raw HAR records.
2. Validate **real elective offerings and teaching-class identifiers**, semester, and seat availability with authenticated read-only evidence.
3. Independently verify the official selection-window state and previously enrolled courses.
4. Validate an institution-permitted enrollment submission contract, subject to its request policy and explicit transaction semantics.
5. Inject a reviewed NCWU adapter into the existing engine and scheduled runner, then implement UI start/cancel/status; never treat offline local readiness as a successful enrollment.
6. Verify final official enrolled-course readback, crash recovery, re-entry idempotency, Windows tray interaction, and resume from sleep or restart.

## Safety and cleanup decisions

Keep account/semester scope, durable journal, nonduplicate write fence, official result verification and request budgets. Keep the website opener and HTML/HAR demos as **manual tools** rather than branding them as automatic selection. Do not merge prior pulse/concurrent bypass experiments into production. Do not discard the historical bootstrap archive until reproducibility has been established.
