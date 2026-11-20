# KingoAssistant · 全自动抢课

**KingoAssistant 是面向华北水利水电大学青果教务系统的全自动抢课软件（Windows 原生 EXE），不是模拟抢课、浏览器自动点击或仅提醒用户手动提交的工具。** 正式执行方案为 **纯 HTTP 接口登录 → 课程与教学班准确匹配 → 选课正式开放后自动提交 → 官方已选记录回读确认**。从登录到结果确认由软件自动执行，不依赖浏览器或人工完成最后一步。

**开发状态说明：** “全自动抢课”是项目的正式定位与唯一目标，不代表当前已通过真实环境验收。华水真实选课提交协议仍待验证，当前版本的正式自动提交功能保持禁用；本地测试与调度程序不等于已完成真实抢课。

> 独立个人项目，与华北水利水电大学及青果软件官方无隶属关系。

## Current status

> **正式执行方案已统一（2026-11-20）：纯接口登录 + 纯接口查询 + 经验证的纯接口提交 + 官方回读。** 浏览器登录混合模式、浏览器选课自动化、CDP/Playwright 和手动官网预约不再是正式备选路线。源码中的旧页面仍需后续清理。[唯一有效架构与验收标准](docs/architecture/pure-api-execution.md)。

- **Windows:** native EXE with in-process HTTP login, school read-only APIs, local course plans, tray lifecycle and offline enrollment FSM. The legacy browser tab, appointments and automatic browser callbacks were removed from the Windows runtime; live NCWU submission remains disabled pending verification.
- **Windows verification:** Verify the latest Windows Actions CI for the current commit; independently passing PRs do not prove their combined GUI works. The packaged smoke test now verifies scheduled-plan preflight remains mounted after merging UI changes.
- **Write gates are now evidence-backed (V5.1):** a gateway capability flag is no longer sufficient. Each of the four gates needs a hash-verified, unexpired local attestation pointing at the document that proved it; editing that document disarms the gate. `python main.py --attest status|issue|revoke` manages the registry offline. Currently **0/4 registered**, so the write path is blocked by missing evidence rather than by missing code. See [attestation design](docs/architecture/attestation.md).
- **Adaptive read cadence V2 (opt-in, no enrollment writes):** successful catalog timings may only slow the next authorized read interval and gradually recover to its approved baseline. School quota checks and unknown-write safeguards remain unchanged; feature disabled by default. See [adaptive V2 design and tests](docs/architecture/adaptive-execution-scheduling-v2.md).
- **Read-only polls are instrumented (V5.1):** per-phase latency samples (`budget_wait` / `request` / `decode` / `parse` / `total`) are written to a local JSONL file with no course identifiers or session data, and summarised into an exported log. The `tools/bench` matrix is why — discovery latency spans 100×, submission latency 2×, and every concurrency strategy measured strictly worse. See [latency instrumentation](docs/architecture/latency-instrumentation.md).
- **Sub-second school-clock offset (offline):** `core/date_flip_sync.py` brackets the school's own `Date` rollover between two samples, replacing the ±500 ms single-header estimate with a bracket whose width is the sampling gap rather than the header's 1 s resolution. It refuses cached (`Age>0`), duplicated and retrograde samples, and issues no requests — the sampler is blocked on the same capture as the catalog. This is the clock half of the sub-second T0 problem that `window_probe` measures; it arms no gate. See [date-flip clock sync](docs/architecture/date-flip-clock-sync.md).
- **Build downloads:** [Windows Actions history](https://github.com/07xh/kingo-assistant/actions/workflows/windows-build.yml); choose a successful run from the commit you intend to use.
- **Official NCWU auth route research (offline):** public school sources confirm a separate CAS authentication host and a teaching-system callback, so the existing single-origin form adapter is not a completed CAS integration. Windows 离线工具 now contains a privacy-preserving local HAR CAS route summary, and `auth.login.mode: cas` blocks unsupported credential submission. See [official evidence and remaining gates](docs/acceptance/ncwu-auth-route-public-20261116.md).
- **EXE-native login/session (phase 2):** the Windows GUI now provides an **EXE 内登录** action using a separately verified, same-origin official form contract, with one bounded HTTP POST and an independent official identity check. Volatile session cookies are reused by read-only identity checks, catalog preparation and monitoring, without storing passwords/cookies on disk or importing browser sessions. It remains disabled until real NCWU login/identity fields and official request policy are verified; generic form code is **not a working NCWU CAS/SSO integration**. See [native login architecture](docs/architecture/native-login-session.md).
- **EXE-native API preparation (phase 1):** the main Windows GUI now has a user-triggered **EXE 接口提前预检（只读）** action. With independently verified protocol settings, an application-owned authorized session and real course IDs, it uses the in-process `KingoClient` to check official identity and read the elective catalog once. It never opens a browser, imports browser cookies or submits enrollment. The later signed-login phase below supersedes the earlier login-adapter status; the native signed login is implemented but not live-accepted, and there is still no real NCWU write adapter. See [native API preparation](docs/architecture/native-api-preparation.md).
- **Login preparation (Windows):** only the independent in-EXE HTTP authentication and verified application-owned session are part of the current architecture. Legacy browser appointments are deprecated and not a fallback. See [pure API login preparation](docs/architecture/login-preparation.md).
- **Android:** planned separately; no APK is implemented or released.
- **EXE native signed login (phase 5, ready for personal live acceptance):** the Windows login button now uses the first-party normal signed-form flow from the Nov 14 HAR: fresh page session, server ephemeral values, compatible legacy encoding, one HTTPS login POST and a matching YearTerm account readback. No browser cookie extraction or manual verified flag toggles. Official CAPTCHA/SMS/CAS challenges stop the workflow. This has **not been verified on the user's live account** and does not enable elective submit. See [native login integration](docs/architecture/ncwu-native-signed-login-20261116.md).
- **EXE account-bound login phase (phase 4):** verified direct-form login can now optionally use the previously observed no-body YearTerm endpoint as its in-process identity readback. Only after independent proof that the endpoint requires authentication and its `userCode` identifies the logged-in account does an exact match with the freshly entered account retain the EXE session; mismatches discard the cookie. This remains disabled until the real official login variant and permitted rate policy are verified. See [identity-binding acceptance](docs/architecture/ncwu-exe-account-binding.md).
- **Original HAR cross-verification (phase 3):** a private offline comparison of the user's Nov 03, Nov 07 and Nov 14 captures revealed two incompatible login POST shapes on the same path, a dynamic `param/param2` timetable request, and a simpler **no-body YearTerm POST** returning `text/plain` JSON. Windows now exposes a one-shot, separately gated YearTerm check in tools; neither this nor older HTML output is identity confirmation or a working elective catalog. Captured dynamic credentials/parameters are **never replayed**. See [sanitized original HAR contract audit](docs/acceptance/ncwu-original-har-contract-audit-20261116.md).
- **Previously captured school HTML readback (phase 2):** a separate exact-path EXE one-shot GET adapter for observed personal timetable entry and selected-course results HTML, using only a validated session and a reviewed query contract. The old HAR redacted the dynamic `params` values, so results remain **disabled by default** until the legitimate query requirements are confirmed. The GUI tool is in **工具与日志**, not the homepage. The school direct form login also checks the observed `loginmethod` field before asking for credentials. This is not an elective-catalog or live enrollment adapter. See [HAR → school HTML readback](docs/architecture/ncwu-har-to-live-timetable.md).
- **Known HAR timetable API wired (read-only):** the Windows EXE now has a real in-process one-shot HTTP adapter for the previously observed NCWU personal-timetable POST `/hsjw//wap/mycourseschedule.action`, with its own GUI button, strict same-origin/session/policy/verified-request-field gates and JSON schema matching. The captured route and output shape were known; the exact safe POST body and EXE-owned CAS session are **still missing**, so the shipped default remains blocked until those are independently validated. This is not elective course availability. See [HAR → live timetable integration](docs/architecture/ncwu-har-to-live-timetable.md).
- **Windows eighth-stage lifecycle quality (2026-11-17):** fixed incomplete session-clear read guards, unified S202/legacy session expiry and auto-refresh shutdown, prevented stale `MonitorWorker` after thread-start failure, and tested stop → finished → restart, with no school HTTP changes. [Regression evidence and scope](docs/acceptance/windows-session-stop-restart-quality-20261117.md).
- **Windows compact UI V2 (2026-11-17):** independent Home/Tools scrolling, 880×560 minimum size, narrow-window sidebar and status wrapping, no business-state changes. Windows CI optionally captures three real Tk screenshots (Home top, Home course list, Tools logs). [Responsive UI architecture and gates](docs/design/windows-responsive-ui-v2-20261117.md).
- **Windows seventh-stage Home layout consolidation (2026-11-17):** the entire Home workflow now uses one `build_home_page(app)` view in the existing `windows_page_sections.py`, without splitting the Application's session, task state or event loop. Updated misleading catalog-readiness text (implemented read-only, still awaits live-account acceptance), and added Tk/worker-safety regressions. [Architecture and acceptance boundaries](docs/architecture/windows-home-layout-extraction-20261117.md).
- **Windows fifth-stage config/runtime/event split (2026-11-17):** moved the UI-independent config template/load paths and the read-only monitor scheduler/event model into standalone modules; monitor status/probe/scheduled-result presentation is factored without changing the GUI event-loop shutdown or session/auth lifecycle. [Architecture and regression gates](docs/architecture/windows-config-monitor-events-extraction-20261117.md).
- **Windows fourth-stage cleanup (2026-11-17):** moved stateless read-only Tk popup/table helpers into `windows_readonly_widgets.py`; timetable, year-term and older selected-page screens now reuse the established native background worker lifecycle without altering their separate `/`-scoped Cookie, contract validation or HTTP adapters. [Audit and regression gates](docs/architecture/windows-fourth-cleanup-legacy-read-workers-20261117.md).
- **NCWU S202 live transport and desktop table cleanup (2026-11-17):** live 2568 catalog now reuses the verified-session read transport and shared native Windows read-worker lifecycle; three result screens share table/popup setup while keeping the exact 2568 form/GBK contract and opt-in ≥60s polling. [Refactor contracts and tests](docs/architecture/ncwu-live-and-ui-table-dedup-20261117.md).
- **NCWU read transport and Windows UI cleanup (2026-11-17):** extracted one shared read-only HTTP client/cookie/budget/error helper and one native worker/result-UI lifecycle for S202 round, personal score, result-page and pre-opening plan reads. Preserved each endpoint, local cooldown, native session, readback scope and separate business parser. [Refactor contract and acceptance](docs/architecture/ncwu-read-ui-dedup-20261117.md).
- **NCWU S202 live catalog ↔ local preference candidates (2026-11-17):** after a real native read of 2568, the Windows popup cross-checks current-window academic term, exact course codes, names and teachers against local targets; highlights only **course candidates**, explicitly keeping the visible `skbh` separate from internal `skbjdm`/class ID, with no extra school request or write. [Readiness tests and limitations](docs/acceptance/ncwu-live-target-review-20261117.md).
- **NCWU course-plan ↔ target preference alignment (2026-11-17):** after reading the official 5327042 *plan snapshot*, the Windows popup checks the school semester against the locally configured `xnxq`, exact course code and school `skbjdm` identifier. A separate detailed review explains missing or mismatching targets without changing YAML, verifying eligibility, or claiming active selection seats. [Acceptance and boundaries](docs/acceptance/ncwu-plan-target-alignment-20261117.md).
- **NCWU missing-interface HAR contract readiness (2026-11-17):** upgraded the offline initializer/eligibility HAR inspector to include URL query field names, POST fields, bounded base64 JSON response structure and an actionable evidence gap state. Windows Tools can copy only the sanitized schema report; it never uploads HAR or enables unverified calls. [Evidence levels and acceptance](docs/acceptance/ncwu-s202-contract-evidence-levels-20261117.md).
- **NCWU historical course-plan snapshot before selection opens:** the EXE now has a separate native read-only `5327042` query based on earlier first-party NCWU evidence, with current-session term/page-derived form data, strict GBK encoding and `qdrs`-only seat values. This is a *plan snapshot*, **not** live selection availability or a successful write. [Plan source and acceptance](docs/acceptance/ncwu-preopen-course-plan-20261117.md).
- **S202 actual selection-count query and offline contract inspection (2026-11-17):** Windows Tools can read the official selection-count summary using current same-session account/term fields, and locally inspect *field names only* from HAR captures for the still-unverified initialization/teaching-class eligibility calls. Neither confirms exact target-class enrollment or enables automatic writes. [Acceptance and field-contract audit](docs/acceptance/ncwu-s202-score-and-contract-capture-20261117.md).
- **NCWU S202 query parser and round diagnostics (2026-11-17):** the EXE now posts its fresh 2568 `ActionForm` as GBK, maps course cells by first-party header IDs even when reordered, and exposes a separate one-shot `getWsxkTimeRange?xktype=2` round-status button (works for closed-window metadata, never proves registration). [Implementation & acceptance boundaries](docs/acceptance/ncwu-s202-round-and-gbk-20261117.md).
- **NCWU S202 selected-results readback (one-shot):** Windows tools now have a separate authenticated GET of the official `/hsjw/student/wsxk.zxjg.jsp` page using the native EXE session. It reports page/table structure and only *candidate* course/class rows when recognizable headings exist; nothing in that response is treated as confirmed registration without independent verification. [Readback implementation and acceptance](docs/acceptance/ncwu-s202-readback-20261117.md).
- **Live NCWU S202 read path (manual, pending real-account acceptance):** Windows now directly reads the current official正选 `ActionForm`, POSTs the first-party window status and DataTable 2568 query, and displays actual returned course/class/status text in a memory-only table. Manual query is available as one bounded read; optional ≥60-second auto-refresh requires a verified request budget. Neither mode writes enrollment or assumes remaining seats. [Live S202 contract / test guide](docs/acceptance/ncwu-live-s202-integration-20261116.md).
- **2026-11-16 NCWU S202 inventory alignment:** local-only 17-column elective-header inspection and GBK login-page decoding fix; verified live course/seat/readback/write contracts are still missing. See [interface inventory audit](docs/acceptance/ncwu-20261116-interface-inventory-audit.md).
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
- Windows navigation contains only **首页 / 工具与日志**; the obsolete manual-official-site tab, menu, appointment callback and auto-launch module were removed. API login, session verification, course configuration and diagnostics remain. Official write/readback acceptance is still required before enabling submission.
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
