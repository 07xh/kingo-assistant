# Acceptance status

Last source baseline: Windows V4.6, plus offline course-catalog evidence comparison tests.

| Gate | Status | Evidence |
|---|---|---|
| Offline unit tests | Passed | **88 tests** passed on Windows 2022 GitHub Actions runner, 2026-11-11 |
| Windows-native EXE build | Passed | PyInstaller one-file GUI build succeeded |
| Packaged EXE offline self-test | Passed | `offline_self_test: passed` |
| Packaged Tk GUI smoke test | Passed | `native_tk_gui_smoke: passed` |
| Native build and artifact | Generated | [Successful Actions run](https://github.com/07xh/kingo-assistant/actions/runs/42980776360) and [artifact](https://github.com/07xh/kingo-assistant/actions/runs/42980776360/artifacts/13836679934), expires 2026-11-25 |
| EXE SHA-256 | Recorded | `5cdeb5f4f6bae7f17cf89b7ab6989a31d5867c9f2e5e35722bfe194f815f91ba` |
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
