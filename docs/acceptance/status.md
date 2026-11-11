# Acceptance status

Last source baseline: Windows V4.6.

| Gate | Status | Evidence / next action |
|---|---|---|
| Offline unit tests | Passed in prepared development environment | 72 tests reported for V4.6 |
| Linux GUI smoke test | Passed in prepared development environment | Not a Windows-native result |
| Windows-native EXE build | Pending | Run the Windows Actions workflow |
| Packaged EXE self-test | Pending | Must pass on a Windows runner |
| Packaged Tk GUI smoke test | Pending | Must pass on a Windows runner |
| NCWU live read-only course catalog | Unverified | Compare with a current authorized official session |
| Remaining-seat field semantics | Unverified | Confirm against official page and response |
| Automatic enrollment submission | Not implemented | Outside current read-only baseline |
| Android client | Planned | No APK yet |

A successful HTTP response or an observed seat count alone is not a successful enrollment. Do not promote an unverified integration to production status.
