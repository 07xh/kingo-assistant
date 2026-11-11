# Acceptance status

Last source baseline: Windows V4.6.

| Gate | Status | Evidence |
|---|---|---|
| Offline unit tests | Passed | 72 tests passed on the Windows 2022 GitHub Actions runner, 2026-11-11 |
| Windows-native EXE build | Passed | PyInstaller one-file GUI build succeeded |
| Packaged EXE offline self-test | Passed | `offline_self_test: passed` |
| Packaged Tk GUI smoke test | Passed | `native_tk_gui_smoke: passed` |
| Artifact | Generated | [Download from Actions](https://github.com/07xh/kingo-assistant/actions/runs/42976926321/artifacts/13835991252); expires 2026-11-25 |
| Artifact SHA-256 | Verified | `df1b028efccaf54d269fa8de68b0f2e0e01ada0cf498fb52dd2fdc9612523da2` |
| NCWU live read-only course catalog | Unverified | Compare with a current authorized official session |
| Remaining-seat field semantics | Unverified | Confirm against official page and response |
| Automatic enrollment submission | Not implemented | Outside current read-only baseline |
| Android client | Planned | No APK yet |

The native build verifies Windows packaging and GUI startup only. It does not verify school login, live course retrieval, seat-count semantics, or enrollment. A successful HTTP response or an observed seat count alone is not a successful enrollment.
