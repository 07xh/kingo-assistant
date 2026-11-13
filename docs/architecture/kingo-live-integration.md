# Kingo-live → KingoAssistant integration: safe, staged reuse

Status: **offline structural analysis integrated; no real enrollment writer installed**.

Source baseline: `07xh/kingo-live@604794482890a92d396d76431ee3c611adfd62eb` (private) and `07xh/kingo-assistant@2ab31adb212d134be0ec050d9404d2a7a4dac4d7` (public).

## Incorporated in this change

- Adapted the bounded JSON list-shape discovery and exact column-role hints from `kingo-live/har_to_schema.py` into `clients/windows/source/core/har_schema.py`. It returns **fixed labels only**; never raw HAR content, endpoint paths, field names, tokens or personal data.
- Reused the existing `core.har_audit.summarize_har` single-pass local loader and existing GUI **离线工具 → 本机 HAR 结构检查**, avoiding a second HAR parser/UI workflow.
- Reuses the existing NCWU timetable exclusion and closed-window evidence logic. Even a matching candidate remains **manual review required**, never a configured API.
- Added synthetic integration tests and CI coverage via the existing Windows workflow.

## Deliberately not imported

- `live_client.py` HTTP submission, `--confirm` / `--auto` CLI, write protocol discovery, session/proxy fan-out, concurrency, request bursts or ignored Retry-After behavior.
- Synthetic benchmark timings as claims about university server speed; the main repository already contains `core/offline_benchmark.py`.
- A parallel Windows GUI, independent configuration store, or a second enrollment state machine.

## Blocking findings in the experimental source

The `kingo-live` intent journal creates a fresh non-persisted HMAC salt across process starts; it writes pending **after** submission instead of before; parallel writes may duplicate an attempt; a single negative enrollment-list readback may be premature. These issues, plus absent institutional endpoint/permission verification, prevent activating any live writer.

## Acceptance boundaries

Running this offline audit does not contact a school server. Candidate shapes cannot set `protocol.verified`, `server_policy.verified`, official selection-window state, actual seat availability, or `EnrollmentEngine` capabilities. Keep the original Windows monitor, scheduler and GUI; preserve `None` for the live engine factory until a separately reviewed authorized adapter passes real acceptance.