# KingoAssistant Windows code audit — 2026-11-11

Scope: `07xh/kingo-assistant` main after PR #19 and #20 merged, plus fixes in the integration branch. This is a source/CI audit, **not** a live NCWU enrollment acceptance.

## Actual execution paths

| Capability | Real code path | Live NCWU status |
|---|---|---|
| Read-only monitor | `windows_gui.Application.start_monitor` → `MonitorWorker` → `core.scheduler.Scheduler` → `KingoClient` | Requires verified read adapter and authenticated session; not enrolled-course submission |
| Official-browser fallback | `toggle_portal_appointment` → GUI timer → `webbrowser.open` | Manual fallback; **not** automatic enrollment |
| Scheduled local plan check | `inspect_scheduled_runner` → `prepare_scheduled_plan` | Pure offline input validation; **does not start a run** |
| Offline automatic enrollment FSM | `core.enrollment_schedule` → `EnrollmentEngine` → `core.enrollment_flow` | Synthetic-gateway tests only; no verified NCWU live writer |
| Tray lifecycle | `desktop_tray.TrayController` → Tk event queue | Remains in-process; Windows must stay running and awake |

## Findings and changes

| Priority | Finding | Action |
|---|---|---|
| P0 | PR #20 inserted a GUI button into obsolete `portal_row`, which PR #19 had replaced with `portal_panel`; this leaves an undefined name on combined main and prevents GUI startup | **Fixed**: mount typed plan preflight in `action_panel`; add packaged GUI smoke invariant |
| P1 | Long lead-in waits woke every 500 ms (7,200 event-loop wakeups per hour) | **Fixed**: 30-second coarse local sleeps for long waits, 5 seconds in mid range and 250 ms only in final 10 seconds; all sleeps remain cancellation-aware. No change to HTTP request rate |
| P1 | Scheduled GUI admission built a runner with `None` engine merely to validate limits | **Fixed**: pure `validate_schedule_limits` reused by UI and runtime; no dummy engine or clock |
| P2 | Windows monitor startup resolved application data paths twice | **Fixed**: runtime config is resolved once by `read_config(..., offline=False)` |
| P2 | Default window still labels itself as old V5.2 website preparation | **Fixed**: display product name and current read-only capability without claiming auto-submission |
| P2 | README claimed the scheduled flow was wholly disconnected, obscuring that GUI now checks typed plans offline | **Fixed**: distinguish wired *preflight* from absent real run |

## Remaining work for genuinely automatic enrollment

1. Independently verify a supported login/session flow scoped to the user's own school account. Do not equate an opened browser with an authenticated script session.
2. Verify authenticated elective catalog and exact teaching-class identifiers plus meaningful remaining-seat field; the existing HAR timetable JSON is not evidence of an open seat.
3. Verify authenticated current selection-window state and already-enrolled records, for the correct semester and account.
4. Obtain and test a permitted enrollment submission contract, keep process/account-scoped pre-write journal and authoritative enrollment-list readback.
5. Connect the live, capability-gated gateway to `run_prepared_schedule`, then add explicit start/cancel/status to the GUI (with no fake green success states).
6. Verify resume-after-sleep, tray restoration and app restart on Windows hardware. A tray is not a Windows service and does not provide restart persistence.
7. Confirm school request rules and quota provenance. Do not substitute self-entered numbers for institutional permission.

## Cleanup boundaries

- Keep read-only monitor and manual browser appointment as explicitly named fallback utilities, not as the default automatic enrollment product flow.
- Keep the FSM, request budget, attempts journal, independent readback and scope checks; removing these would risk duplicate writes, account confusion or false success.
- Do **not** merge legacy high-frequency polling PR #6 simply for speed: it predates the independently validated budget contract. Old baseline PR #9 and browser-driven early-login PR #18 require compatibility/intent review before promotion.
- Keep historical source snapshot until reproducibility and source equivalence are checked; do not delete a binary archive by filename guess.
- All regression tests are synthetic and must be supplemented by a school-approved end-to-end acceptance before enabling live writes.
