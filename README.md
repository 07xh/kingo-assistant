# KingoAssistant

KingoAssistant is an independent personal course-selection assistant for the NCWU (North China University of Water Resources and Electric Power) academic system.

> This project is not affiliated with NCWU or Kingosoft.

## Current status

- **Windows:** Desktop course plans, read-only monitoring, native tray minimization, an offline automatic-enrollment plan preflight, and a separately tested enrollment state machine. Closing the window keeps the current task; explicit tray quit stops it.
- **Windows verification:** Verify the latest Windows Actions CI for the current commit; independently passing PRs do not prove their combined GUI works. The packaged smoke test now verifies scheduled-plan preflight remains mounted after merging UI changes.
- **Build downloads:** [Windows Actions history](https://github.com/07xh/kingo-assistant/actions/workflows/windows-build.yml); choose a successful run from the commit you intend to use.
- **Login preparation (Windows):** appointment fallback can open the fixed official website 15 minutes early for the student to log in and again at the confirmed time. A separate **user-triggered, read-only application-session check** is available only for a verified official identity endpoint. Browser sessions are never silently imported; this does not submit enrollment. See [login preparation](docs/architecture/login-preparation.md).
- **Android:** planned separately; no APK is implemented or released.
- **School integration:** the 2026-11-11 HAR confirms a timetable JSON response and selection-related metadata, but does not verify a live elective catalog, a remaining-seat field, or a permitted write/confirmation contract.
- **Kingo-live research integration (offline only):** the Windows **local HAR structure check** now reuses bounded JSON list-shape detection and field-role hints from `07xh/kingo-live`, displaying only anonymous counts and timetable/closed-window exclusions. No live protocol parameters, account data or automatic enrollment actions are imported. [Scope and acceptance notes](docs/architecture/kingo-live-integration.md).
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

The 2026-11-11 HAR included an explicit “current time is not a valid selection window” response. Timetable data and `zxinfo.zxxq` records are not an elective catalog or seat count. See [the sanitized HAR audit](docs/acceptance/ncwu-xiqueer-har-2026-11-11.md) and [the execution architecture](docs/architecture/automatic-enrollment.md).

A separate scheduled runner can wait for a configured time and check a verified selection-window state at a bounded 60–3600 second interval. The Windows EXE now includes a **threaded, in-process enrollment execution controller** with explicit start/cancel, completion events, and tray-aware shutdown. Its start button remains **disabled by default** because no verified NCWU write-adapter factory is shipped; local plan preflight cannot enable school submissions. A stop request cancels waiting, but does not interrupt an in-flight write/readback. See [scheduled-runner design](docs/architecture/scheduled-enrollment.md) and [native Windows executor](docs/architecture/windows-scheduled-execution.md).

## Official browser fallback for the next selection round

Windows V5.2 adds an **official-site appointment**: after you enter a personally confirmed opening time, the running app counts down and opens the fixed official NCWU page once. You can copy your stored first-choice course code, move between ordered alternatives, and submit through the official website. This does **not** submit automatically or claim a verified live selection interface. Keep the computer awake and the client running; see [official-site fallback guide](docs/acceptance/official-portal-appointment.md).

## Official entry diagnostics (manual; phase 1)

NCWU's official selection notice lists the direct academic-system website,
unified authentication and the official 喜鹊儿 app. Those are *login
surfaces*, not evidence of three independent catalog/selection servers.

The Windows source CLI offers `--official-entries` (offline inventory, no
config required) and `--probe-official-entries` (explicit one-shot login-page
reachability checks; at most two requests, no credentials or redirect
following). Login-page HTTP success does **not** verify school catalog access.

The Windows standalone GUI also exposes **离线工具 → 华水官方入口清单**
and **离线工具 → 检测官方登录页可达性** (the latter requires confirmation
and uses a background thread so the desktop stays responsive).

A guarded read-only route planner and synthetic tests are present, but only
one documented web catalog candidate is registered. Live automatic failover
and any actual enrollment POST are **not enabled**. See
[the route design and acceptance gates](docs/architecture/official-entry-routing.md).

## Windows client

启动只读监测时，点击一次“启动只读监测”即可明确启动当次任务；本机预检会在同一份配置快照上完成，非关键的人工节次提示不会弹出二次确认框。本人会话、已核实只读协议和请求预算仍是启动前提；这不代表自动提交已接入。

- Course plans are stored locally and do not include credentials or school protocol settings.
- Course editor order is the preference order (top to bottom); courses in the same non-empty alternative group are mutually exclusive alternatives. Use the editor's **上移 / 下移** controls to change priority; saved plans preserve this order.
- Current live client remains read-only and enforces a minimum local polling interval of 60 seconds. This is a client-side safety floor, not a claim about the school's allowed maximum. Follow any stricter official policy.
- Offline HAR and catalog-evidence tools never replay a capture or contact the school.

## Repository layout

- `clients/windows/source/` — Windows source and tests.
- `clients/windows/kingo-windows-v4.6.tar.xz` — immutable source bootstrap snapshot.
- `clients/android/` — future Android client.
- `docs/architecture/` — architecture decisions.
- `docs/acceptance/` — evidence and acceptance gates.
- `.github/workflows/windows-build.yml` — native Windows build and packaged-EXE verification.

## Windows desktop behavior (tray / simplified UI)

- Closing the main window **minimizes to the Windows system tray** if the native tray backend is available. The running read-only monitor and one-shot official-browser appointment are not canceled by that action.
- Use the tray menu **显示主窗口** to restore the UI and **完全退出** to stop tasks and exit. If the tray backend cannot initialize, the application minimizes to the normal taskbar instead of becoming invisible.
- The main screen separates course plans and **只读监测** from the **手动辅助** menu (official-site opening / copied course codes) and **离线工具**. Advanced YAML controls are hidden under **程序 → 显示/隐藏高级配置**; read-only polling knobs are collapsed by default in the course editor.
- These UI improvements **do not enable automated school enrollment**. The NCWU live submission adapter is still unverified and disabled. Tray background operation also requires Windows to stay awake; it is not a background service or a reboot-persistent appointment.

## Engineering audit

The [2026-11-11 Windows integration audit](docs/acceptance/windows-integration-audit-2026-11-11.md) separates fixed UI integration defects from remaining NCWU live-enrollment requirements. **Offline scheduling is not proof of an approved submission capability.**

## Windows build

1. Open the **Actions** tab.
2. Select **Windows standalone EXE**.
3. Run the workflow manually or use a successful push/PR run.
4. Download the `KingoAssistant-Windows-portable` artifact from that run.

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
