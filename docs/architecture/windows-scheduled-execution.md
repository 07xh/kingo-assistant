# Windows in-process scheduled execution integration

Status: **worker and GUI seam implemented, school-specific engine factory missing** (2026-11-11).

## Real execution chain (once an authorized NCWU adapter is available)

`Application.start_scheduled_enrollment`
→ one offline `prepare_scheduled_plan` snapshot
→ explicit per-run user consent
→ `DesktopScheduledWorker` on a separate Python thread with its own `asyncio` loop
→ **injected** `EnrollmentEngine` constructor
→ mandatory account/permission/verified-gateway checks
→ `run_prepared_schedule`
→ `ScheduledEnrollmentRunner` wait for local start and independently verified official selection-window state
→ `EnrollmentEngine` / FSM / durable pre-write journal / official enrollment-list readback
→ redacted result events delivered to Tk on its queue.

No browser opening, guessed URL, direct HAR replay, or unapproved NCWU request is part of this chain.

The application intentionally initializes `_engine_factory=None` and disables the native start button. **Local green plan validation never sets capabilities or installs a factory**. A reviewed school-specific implementation must install the factory and supply its permission evidence, official identity, catalog, window, exact teaching-class matching, current enrollments, and permitted write contract. Do not add a mock adapter to the production startup path.

## Cancellation and app lifecycle

- A user cancel action signals an event inside the worker's loop; pre-start and wait-for-window phases stop without submitting.
- If an official write/readback is in flight, a stop request does not cancel it, because the result may be indeterminate and an interrupted readback can lead to unsafe duplicate submission later.
- A scheduled task uses the same process as the Windows EXE. Closing the window preserves the thread in tray; explicit tray exit requests cancellation and waits for the worker's completion event.
- Starting an in-process run cancels any previously armed manual-browser appointment. Starting a new manual-browser appointment while a native scheduled run is active is blocked.
- Runtime does not survive process exit, machine reboot or Windows sleep. Durable restart handoff remains an unimplemented requirement.

## Test matrix

- Missing production adapter: start button disabled in packaged GUI, direct launch refuses before accessing any config/network.
- Factory returns unverified gateway or wrong engine type: blocked before timer/school requests.
- Valid injected synthetic engine: background event order and result propagation tested offline.
- Cancel before worker starts / during wait: tested, with no school connection.
- GUI manual consent refusal: no worker created; consent acceptance spawns a worker without `webbrowser.open`.
- Error redaction: only local exception type may reach GUI; credentials/response payloads never reach logs.
- Adapter cleanup is attempted, including on failed readiness, without transforming an authoritative result into a false success.
- Exit waits for worker to finish, rather than abandoning uncertain writes.

## Remaining external acceptance

Use an independently authorized NCWU integration to verify the real authenticated elective catalog, official selection-window status, current enrolled-course records, course/class IDs, response semantics and school request policy. Then connect its factory in a reviewed change and perform a permitted end-to-end test on Windows. CI's fake gateway cannot establish real availability or successful course enrollment.
