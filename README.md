# KingoAssistant · 全自动抢课

**KingoAssistant 是面向华北水利水电大学青果教务系统的全自动抢课软件（Windows 原生 EXE），不是模拟抢课、浏览器自动点击或仅提醒用户手动提交的工具。** 正式执行方案为 **纯 HTTP 接口登录 → 课程与教学班准确匹配 → 选课正式开放后自动提交 → 官方已选记录回读确认**。从登录到结果确认由软件自动执行，不依赖浏览器或人工完成最后一步。

**开发状态说明：** “全自动抢课”是项目的正式定位与唯一目标，不代表当前已通过真实环境验收。华水真实选课提交协议仍待验证，当前版本的正式自动提交功能保持禁用；本地测试与调度程序不等于已完成真实抢课。

> 独立个人项目，与华北水利水电大学及青果软件官方无隶属关系。

## 当前状态

- **Windows 工作台**有「选课工作台」和「工具与日志」两页。选课计划、教务账号、读取课程和只读监测共用主界面；定时计划与提交验收状态固定在底部。[界面与验收](docs/design/windows-dense-desktop-ui-20261119.md)
- **界面开发路线已确定：** 使用当前 Tkinter/ttk 紧凑 Win32 密集工作台（PR #89）作为唯一 Windows UI 基线；不再规划 React + Tauri Lite、液态玻璃或现代侧栏 UI 迁移。此前 Lite 方案 PR #47 已关闭，旧方案仅保留在 Git 历史中，不作为开发或构建目标。
- **校内读写采用进程内 HTTP。** 只读监测须使用应用自身核验的会话，并满足协议和请求预算。正式写入及官方结果回读仍缺少端到端验收，当前不能启用真实提交。[执行架构](docs/architecture/pure-api-execution.md)
- **验收以当前提交为准。** 真实登录、查询、提交、回读分别核验。离线演示、CI 和 EXE 构建只能证明各自的范围。[验收记录](docs/acceptance) · [Windows Actions](https://github.com/07xh/kingo-assistant/actions/workflows/windows-build.yml)
- **Android** 单独规划，目前没有发布 APK。

## Automatic enrollment architecture

The engine requires all of the following before a school adapter can be used:

1. Explicit user opt-in for the current run.
2. Verified institutional permission and published request limits.
3. Independently verified official catalog, current enrollment records, selection-window state, and write contract.
4. Official selection window reported as open, checked again immediately before each write.
5. Exact course + teaching-class matching and a durable pre-write intent journal.
6. Official enrollment-list readback to confirm success; transport acknowledgements alone are insufficient.
7. Serial writes only. Explicit rejection may move to the next configured preference after a fresh catalog read. Unknown outcomes stop and are never automatically retried.

Gates 3 and 4 additionally require a **local evidence attestation**. A gateway
flag alone is no longer sufficient: each of the four capability gates must be
covered by a hash-verified, unexpired record pointing at the document that proves
it. Editing that document disarms the gate until it is reissued. Manage the
registry offline — no config file and no network needed:

```bash
python main.py --attest status
python main.py --attest issue --gate official_write_verified \
    --evidence docs/acceptance/ncwu-write-contract.md --reviewer <you> --days 90
python main.py --attest revoke --gate official_write_verified
```

See [docs/architecture/attestation.md](docs/architecture/attestation.md). As of
this commit **0 of 4 gates are registered**, so the write path is blocked by
missing evidence rather than by missing code — arming all four still cannot
submit, because no NCWU write adapter is shipped.

Read-only polls now emit per-phase latency samples (`budget_wait` / `request` /
`decode` / `parse` / `total`) to a local JSONL file, with no course identifiers or
session data recorded; see
[docs/architecture/latency-instrumentation.md](docs/architecture/latency-instrumentation.md).
The benchmark matrix in `tools/bench` is why: in that simulator, discovery
latency spans two orders of magnitude (60 s vs 500 ms poll intervals) while the
submission pipeline spans 2×, and every concurrency strategy lowered the
per-request confirmation share and raised pipeline p95 (8 concurrent: 1 of 8
requests confirmed, p95 70 ms → 1178 ms).

Read `success_rate` there as "confirmed requests / all requests", never as "the
probability this student gets the course" — the two differ, and only the latter
matters to a student. `total_p95` likewise sums client+network+queue+tx only; it
excludes discovery wait and readback, so it is not end-to-end latency. The 500 ms
poll interval is a simulation input, not a deployable setting: the client enforces
a 30 s floor and has no verified school frequency permission.

The 2026-11-11 HAR included an explicit “current time is not a valid selection window” response. Timetable data and `zxinfo.zxxq` records are not an elective catalog or seat count. See [the sanitized HAR audit](docs/acceptance/ncwu-xiqueer-har-2026-11-11.md) and [the execution architecture](docs/architecture/automatic-enrollment.md).

A separate scheduled runner can wait for a configured time and check a verified selection-window state at a bounded 60–3600 second interval. The Windows EXE now includes a **threaded, in-process enrollment execution controller** with explicit start/cancel, completion events, and tray-aware shutdown. Its start button remains **disabled by default** because no verified NCWU write-adapter factory is shipped; local plan preflight cannot enable school submissions. A stop request cancels waiting, but does not interrupt an in-flight write/readback. See [scheduled-runner design](docs/architecture/scheduled-enrollment.md) and [native Windows executor](docs/architecture/windows-scheduled-execution.md).

## 唯一执行链（纯 HTTP）

从 EXE 自有 HTTP 登录，到课程读取、合法时间窗内的正式提交、官方回读，全程不依赖浏览器。旧版“预约打开官网”已废止为技术方案，剩余源码需要移除。**当前真实 NCWU 提交协议仍未完成验收，不能宣称软件已经具备可用的正式自动选课能力。** 详见 [正式架构](docs/architecture/pure-api-execution.md)。

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
- `clients/windows/README.md` — 当前 Windows 源码、独立 EXE 构建方式与真实验收边界（已移除旧 V4.6 启动压缩包，历史版本可从 Git 历史查看）。
- `clients/android/` — future Android client.
- `docs/architecture/` — architecture decisions.
- `docs/acceptance/` — evidence and acceptance gates.
- `.github/workflows/windows-build.yml` — native Windows build and packaged-EXE verification.

## Windows desktop behavior (tray / simplified UI)

- Closing the main window **minimizes to the Windows system tray** if supported. The in-process HTTP monitor retains its existing lifecycle; deprecated browser appointments are not a supported target workflow.
- Use the tray menu **显示主窗口** to restore the UI and **完全退出** to stop tasks and exit. If the tray backend cannot initialize, the application minimizes to the normal taskbar instead of becoming invisible.
- Windows navigation contains only **选课工作台 / 工具与日志**; the obsolete manual-official-site tab, menu, appointment callback and auto-launch module were removed. API login, session verification, course configuration and diagnostics remain. Official write/readback acceptance is still required before enabling submission.
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
