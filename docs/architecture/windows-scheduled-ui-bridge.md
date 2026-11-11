# Windows scheduled admission bridge

Status: **typed local plan and Windows preflight implemented; no NCWU live adapter**.

The `core.scheduled_ui_bridge` module provides:

1. `prepare_scheduled_plan(config)`: validates user-confirmed future start time, exact course and teaching-class IDs, ordered course alternatives, semester identity, and `ScheduledEnrollmentRunner`'s 60–3600-second window polling / 1–1440-minute wait bounds. It makes **zero network requests** and never calls the enrollment engine.
2. `run_prepared_schedule(plan, engine, stop_event)`: connects the typed local plan to the actual `ScheduledEnrollmentRunner`. It accepts only an **explicitly injected** `EnrollmentEngine`; the engine must independently pass user authorization, institutional permission, and the official catalog, selection-window, enrollment-readback and write capability gates before waiting or contacting the school.
3. Windows GUI control **自动选课计划预检（离线）**: validates current saved plan using the bridge and prints blockers. It **does not** call `run_prepared_schedule`, launch a background worker, create a network client, or claim an automatic-enrollment session is running.

The existing **预约按时打开官网（辅助）** remains a separate manual fallback and must not be conflated with the real scheduled runner. No estimated selection date is silently treated as an actual open window.

## Next gates

- Independent authorized read-only observation of current catalog, selection window, enrolled-course list, and corresponding account identity.
- Real gateway construction gated by official written permission and a verified request contract; no raw HAR credentials in source/configuration.
- UI integration for run/cancel/status **only after** a real adapter exists; prevent a green local preflight from being interpreted as an active job.
- Native Windows EXE CI, GUI smoke tests and real-world read-only verification before release.

See [school schedule evidence](../acceptance/official-selection-window-evidence-2026-11.md) for the official dates and limitations.
