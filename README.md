# KingoAssistant

KingoAssistant is an independent personal course-selection assistant for the NCWU (North China University of Water Resources and Electric Power) academic system.

> This project is not affiliated with NCWU or Kingosoft.

## Current status

- **Windows:** V5.1 work builds on the V5.0 mainline: local course plans, preflight checks, read-only monitoring, and a fail-closed automatic-enrollment state machine.
- **Windows verification:** The V5.0 + enrollment-core mainline passed 133 offline tests and a native Windows EXE / Tk GUI smoke test. V5.1 adds official-window gating, persistent attempt-state transitions, OS-backed process locking, and editable priority order; its CI result is tracked on the PR.
- **Latest successful mainline build:** [Windows standalone EXE workflow](https://github.com/07xh/kingo-assistant/actions/runs/42991429669).
- **Android:** planned separately; no APK is implemented or released.
- **School integration:** the 2026-11-11 HAR confirms a timetable JSON response and selection-related metadata, but does not verify a live elective catalog, a remaining-seat field, or a permitted write/confirmation contract.
- **Automatic enrollment:** orchestration logic exists and is tested with synthetic gateways. **No NCWU live write adapter is implemented or enabled.** The Windows client remains read-only; do not interpret a build or offline test as a successful real enrollment.

## Automatic enrollment architecture

The engine requires all of the following before a school adapter can be used:

1. Explicit user opt-in for the current run.
2. Verified institutional permission and published request limits.
3. Independently verified official catalog, current enrollment records, selection-window state, and write contract.
4. Official selection window reported as open, checked again immediately before each write.
5. Exact course + teaching-class matching and a durable pre-write intent journal.
6. Official enrollment-list readback to confirm success; transport acknowledgements alone are insufficient.
7. Serial writes only. Explicit rejection may move to the next configured preference after a fresh catalog read. Unknown outcomes stop and are never automatically retried.

The 2026-11-11 HAR included an explicit “current time is not a valid selection window” response. Timetable data and \`zxinfo.zxxq\` records are not an elective catalog or seat count. See [the sanitized HAR audit](docs/acceptance/ncwu-xiqueer-har-2026-11-11.md) and [the execution architecture](docs/architecture/automatic-enrollment.md).

## Windows client

- Course plans are stored locally and do not include credentials or school protocol settings.
- Course editor order is the preference order (top to bottom); courses in the same non-empty alternative group are mutually exclusive alternatives. Use the editor's **上移 / 下移** controls to change priority; saved plans preserve this order.
- Current live client remains read-only and enforces a minimum local polling interval of 60 seconds. This is a client-side safety floor, not a claim about the school's allowed maximum. Follow any stricter official policy.
- Offline HAR and catalog-evidence tools never replay a capture or contact the school.

## Repository layout

- \`clients/windows/source/\` — Windows source and tests.
- \`clients/windows/kingo-windows-v4.6.tar.xz\` — immutable source bootstrap snapshot.
- \`clients/android/\` — future Android client.
- \`docs/architecture/\` — architecture decisions.
- \`docs/acceptance/\` — evidence and acceptance gates.
- \`.github/workflows/windows-build.yml\` — native Windows build and packaged-EXE verification.

## Windows build

1. Open the **Actions** tab.
2. Select **Windows standalone EXE**.
3. Run the workflow manually or use a successful push/PR run.
4. Download the \`KingoAssistant-Windows-portable\` artifact from that run.

The workflow runs offline unit tests, builds the EXE on Windows, and starts the packaged executable with offline self-test and Tk GUI smoke-test flags. Build artifacts are temporary; stable releases should be published as GitHub Releases after review.

## Safety and privacy

- Use only your own authorized account and officially permitted access.
- Respect official selection windows, terms, access controls, and published request limits.
- No CAPTCHA bypass, authentication or access-control bypass, rate-limit evasion, or high-frequency request bursts.
- Never commit passwords, cookies, tokens, signatures, raw HAR files, personal course data, or student records.
- Use synthetic fixtures in tests and redact diagnostics before sharing.

See [SECURITY.md](SECURITY.md) before reporting a security issue.

## License

No project-wide license has been selected yet. Public visibility does not grant permission to reuse or redistribute this code. Third-party dependency licenses remain applicable.
