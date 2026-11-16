# Acceptance status

Last source baseline: Windows V4.6, plus offline course-catalog evidence comparison tests.

| Gate | Status | Evidence |
|---|---|---|
| Offline unit tests | Passed | **231 tests** passed in the author's local environment (272 collected; the remaining 41 are GUI/HTTP modules that need `httpx`/`tkinter`/`yaml`, which that sandbox lacked — CI runs them) |
| Windows-native EXE build | Passed | PyInstaller one-file GUI build succeeded |
| Packaged EXE offline self-test | Passed | `offline_self_test: passed` |
| Packaged Tk GUI smoke test | Passed | `native_tk_gui_smoke: passed` |
| Native build and artifact | Generated | [Successful Actions run](https://github.com/07xh/kingo-assistant/actions/runs/42980776360) and [artifact](https://github.com/07xh/kingo-assistant/actions/runs/42980776360/artifacts/13836679934), expires 2026-11-25 |
| EXE SHA-256 | Recorded | `5cdeb5f4f6bae7f17cf89b7ab6989a31d5867c9f2e5e35722bfe194f815f91ba` |
| Write-gate attestation registry | Implemented, offline | [Design](../architecture/attestation.md); 28 tests; `python main.py --attest status` |
| Evidence for the four capability gates | **0/4 registered** | No evidence document has been issued yet, so the write path is now closed by missing evidence rather than by missing code |
| Latency instrumentation | Implemented | [Design](../architecture/latency-instrumentation.md); per-phase JSONL + exported-log summary; 24 tests |
| Pickup polling schedule | Implemented, disabled by default | Peak windows clamp into `interval_range` and never go below the 30 s floor; 8 tests |
| Plan v1 metric corrections | Passed | [Corrections log](plan-v1-review-corrections-20261116.md); 5 review findings verified against code, 3 were real errors in the v1 plan and are fixed in the shipped docs |
| Window boundary estimation | Implemented (arithmetic), not wired | [Design](../architecture/window-boundary-estimation.md); brackets the CLOSED→OPEN flip from a probe burst; 26 tests. Probe source blocked on the same capture as the catalog |
| Sub-second school-clock offset | Implemented (arithmetic), not wired | [Design](../architecture/date-flip-clock-sync.md); brackets the `Date` rollover between two samples instead of trusting a 1 s header, so a probe bracket is not swamped by clock error; refuses cached, duplicated and retrograde samples; 25 tests. Sampler blocked on the same capture as the catalog |
| Latency report consumer | Implemented | `python main.py --latency [file|dir] [--compare base] [--json]`; per-phase percentiles, bottleneck verdict, budget-wait share; 26 tests |
| Watcher decision rules extracted | Refactor, behaviour unchanged | `core/release_watch.py` holds matching / classification / alert dedup so it can be tested without httpx; `Scheduler` keeps I/O only; 23 tests. `Scheduler.matches` and `_already_notified` kept as compatible aliases |
| Offline read-only catalog evidence helper | Ready for manual use | [Verification guide](catalog-readonly-verification.md); 10 new synthetic unit tests |
| Offline Xiqueer HAR structure audit | Passed locally with user's prior capture | 67 entries, 55 gateway requests, 1 timetable schema match, 4 closed-window messages; see [guide](har-offline-audit.md) |
| Offline GUI evidence review | Passed | Available under Windows 离线工具; 6 new synthetic tests across HAR + GUI |
| NCWU official selection window | Not yet confirmed open for user | School's 2026-2027 second-term plan lists December 21–25, 2026 for elective/sports selection; precise grade/time depends on later announcements |
| NCWU live read-only course catalog | **Unverified** | Need to compare authorized official UI against a permitted response during the actual window |
| Remaining-seat field semantics | **Unverified** | A selected-course record, course capacity or HTTP 200 is not an available-seat field |
| Automatic enrollment submission | Not implemented | Outside current read-only baseline |
| Android client | Planned | No APK yet |

The **offline evidence checker never sets a verified API flag**. Even two matching anonymous samples remain `ready_for_manual_review`, not proof of an official course-catalog API. The native build verifies Windows packaging and GUI startup only, not live school login, course availability or enrollment.

No original HAR, student data, cookies, tokens, signed parameters, or private course evidence was added to this public repository.
