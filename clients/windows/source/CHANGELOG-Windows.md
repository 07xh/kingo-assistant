# Windows desktop changelog

## 未发布 · 外壳 v2 + 网络与时间面板

- Visual shell v2: token scale (spacing / type / semantic colour), card and pill primitives, three button tiers (`KingoPrimary` / `KingoQuiet` / `KingoDanger`), thin scrollbars, table heading states, hover feedback on the sidebar rail and a read-only state block above the footer.
- Header now shows a state pill next to the title and keeps the existing breadcrumb / subtitle contract used by the responsive layout tests.
- New Tools-page card for three offline measurements: candidate-node handshake ranking, server clock offset from response headers, and session keepalive with conservative relogin.
- `core/netops/` is standard-library only. Ranking uses the median of three rounds and drops nodes failing more than half the probes; clock offset is kept in memory and never written to the system clock; relogin is limited to explicit authentication failures with capped exponential backoff.
- Worker threads publish results through a queue only; the panel touches widgets exclusively from the Tk main thread. Nothing measures at build time.
- The panel's relogin hook returns failure on purpose: this client ships no verified login adapter, so the status line says so instead of pretending a session was restored.
- Page builders (`windows_page_sections.py`) are untouched widget-for-widget so the existing layout contract tests keep asserting the same trees.
- Offline tests: 31 new cases in `tests/test_windows_netops.py`, including real Tk widget tests; the 9 existing shell / scrolling tests still pass unchanged.

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
