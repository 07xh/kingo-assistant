# KingoAssistant

KingoAssistant is an independent course-information assistant project for personal use with the NCWU (North China University of Water Resources and Electric Power) academic system.

> This project is not affiliated with NCWU or Kingosoft.

## Current status

- **Windows:** V4.6 baseline plus new offline HAR/selection-evidence tools. Source tree is present in `clients/windows/source/`. A native Windows 2022 build succeeded on 2026-11-11.
- **Windows verification:** 88 offline unit tests passed; the packaged EXE passed its offline self-test and native Tk GUI smoke test.
- **Build artifact:** [KingoAssistant-Windows-portable](https://github.com/07xh/kingo-assistant/actions/runs/42980776360/artifacts/13836679934) (temporary GitHub Actions artifact; expires 2026-11-25).
- **Android:** planned as a separate client; no APK is implemented or released yet.
- **School integration:** live NCWU course-catalog and remaining-seat endpoints have not been verified.
- **Enrollment writes:** not implemented. The current client is a read-only watcher and reminder; a detected seat count is not an enrollment confirmation.

The Windows EXE is a verified packaging build, not a verified NCWU integration or enrollment tool.

## Repository layout

- `clients/windows/source/` — browsable Windows V4.6 source.
- `clients/windows/kingo-windows-v4.6.tar.xz` — immutable source bootstrap snapshot.
- `clients/android/` — Android client planning and future implementation.
- `docs/architecture/` — architecture decisions.
- `docs/acceptance/` — verification status and acceptance gates.
- `.github/workflows/windows-build.yml` — Windows-native build and packaged-EXE verification.

## Windows build

1. Open the **Actions** tab.
2. Select **Windows standalone EXE**.
3. Run the workflow manually, or push changes to the configured branches.
4. Download the `KingoAssistant-Windows-portable` artifact from a successful run.

The workflow runs offline tests, builds the EXE on Windows, and starts the packaged executable with offline self-test and Tk GUI smoke-test flags. The current artifact is retained for 14 days; stable releases should be published as GitHub Releases after review.

## Offline evidence tools

- Windows **离线工具 → 本机 HAR 结构检查（脱敏统计）**: locally counts NCWU timetable-schema responses, Xiqueer gateway traffic, and selection-window restriction messages. It never displays the raw response or sends a network request.
- Windows **离线工具 → 选课目录证据对照（离线 JSON）**: checks anonymous, sanitized official-UI-vs-response evidence and still requires human verification.
- [Local HAR audit guide](docs/acceptance/har-offline-audit.md) and [catalog verification rules](docs/acceptance/catalog-readonly-verification.md).
- Neither tool makes the live elective catalog or seat-count integration verified.

## Safety and privacy boundaries

- Use only your own authorized account and official, permitted read-only access.
- Respect school schedules, terms of service, access controls, and published request limits.
- No CAPTCHA bypass, access-control bypass, rate-limit evasion, or high-frequency request bursts.
- Never commit passwords, cookies, tokens, raw HAR files, personal course data, or student records.
- Use synthetic fixtures for tests. Redact sensitive values before sharing diagnostics.

See [SECURITY.md](SECURITY.md) before reporting a security issue.

## License

No project-wide license has been selected yet. Public visibility does not grant permission to reuse or redistribute this code. Third-party dependency licenses remain applicable.
