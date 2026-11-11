# KingoAssistant

KingoAssistant is an independent course-information assistant project for personal use with the NCWU (North China University of Water Resources and Electric Power) academic system.

> This project is not affiliated with NCWU or Kingosoft.

## Current status

- **Windows:** V4.6 source snapshot imported. The first Windows Actions run expands the snapshot into `clients/windows/source/`, commits the browsable source tree to the same branch, and builds a portable EXE.
- **Android:** planned as a separate client; no APK is implemented or released yet.
- **School integration:** live NCWU course-catalog and remaining-seat endpoints have not been verified.
- **Enrollment writes:** not implemented. The current client is a read-only watcher and reminder; a detected seat count is not an enrollment confirmation.
- **Windows native verification:** pending the first successful Windows Actions run.

The V4.6 source snapshot passed 72 offline unit tests in the prepared development environment. That is not evidence of a successful Windows-native build or a live school-system connection.

## Repository layout

- `clients/windows/kingo-windows-v4.6.tar.xz` — immutable bootstrap snapshot for the Windows V4.6 source.
- `clients/windows/source/` — expanded Windows source tree, created by the first successful Windows build workflow.
- `clients/android/` — Android client planning and future implementation.
- `docs/architecture/` — architecture decisions.
- `docs/acceptance/` — verification status and acceptance gates.
- `.github/workflows/windows-build.yml` — Windows-native build and packaged-EXE verification.

## Windows build

1. Open the **Actions** tab.
2. Select **Windows standalone EXE**.
3. Run the workflow manually, or let it run after a push to the import branch.
4. Download the `KingoAssistant-Windows-portable` artifact from a successful run.

On its first run, the workflow extracts the V4.6 snapshot to `clients/windows/source/` and commits those source files to the same branch. It then runs the offline tests, builds the EXE on Windows, and starts the packaged executable with offline self-test and Tk GUI smoke-test flags.

The build artifact is temporary (14-day retention). Stable releases should be published as GitHub Releases after native verification.

## Safety and privacy boundaries

- Use only your own authorized account and official, permitted read-only access.
- Respect school schedules, terms of service, access controls, and published request limits.
- No CAPTCHA bypass, access-control bypass, rate-limit evasion, or high-frequency request bursts.
- Never commit passwords, cookies, tokens, raw HAR files, personal course data, or student records.
- Use synthetic fixtures for tests. Redact sensitive values before sharing diagnostics.

See [SECURITY.md](SECURITY.md) before reporting a security issue.

## License

No project-wide license has been selected yet. Public visibility does not grant permission to reuse or redistribute this code. Third-party dependency licenses remain applicable.
