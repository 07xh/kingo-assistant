# Windows desktop changelog

## V4.3 (2026-11-11)

- Added an offline graphical editor for targets, teaching-class IDs, semester, low-frequency interval and one-time scheduled check.
- Course edits preserve optional group metadata, prevent duplicate keys and require at least one matching field.
- Added atomic YAML save with permissions best effort and removal of embedded Cookie / push tokens; environment variables remain supported.
- Resolved relative notification state-file paths inside the user application-data directory instead of the EXE working directory; path traversal is rejected.
- Added local desktop bell on transition to availability, request/parse elapsed-time visibility, and an exportable redacted GUI status log.
- Closing while monitoring now waits for worker shutdown before destroying the window.
- Added Windows GitHub Actions artifact build and improved build scripts with unit-test gate and SHA256 output.
- Offline tests expanded from 43 to 53. Windows EXE and real NCWU endpoints remain unverified.

## V4.2 (2026-11-11)

- First standalone Tkinter Windows desktop GUI for existing V4.1 read-only core, config checks, offline replay, limited monitoring, thread-safe status updates.
