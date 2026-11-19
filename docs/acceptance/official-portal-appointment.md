# Retired: official-site browser appointment

Status: **removed from the Windows GUI** in the API-only workflow cleanup (2026-11-20).

The historical V5.2 implementation opened the official academic website at a configured time, then required manual login and manual enrollment. This was not an API submission, so it is no longer offered as a KingoAssistant selection mode.

The Windows client now exposes an **接口执行** page containing application-owned session verification, offline scheduled-plan validation, and live-adapter acceptance details. Its official submit button remains disabled until the verified NCWU transaction adapter and authoritative readback have passed the separately documented acceptance gates.

No browser, clipboard handoff, guessed submission form, simulated server success, or web-navigation fallback is part of the production enrollment chain. The test and source history remain in Git.

See [automatic enrollment](../architecture/automatic-enrollment.md) and [scheduled Windows execution](../architecture/windows-scheduled-execution.md).
