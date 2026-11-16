# NCWU S202 interface-list integration audit · 2026-11-16

Source: the owner's locally supplied 2026-11-16 interface inventory (22:28–23:05). This is an **evidence audit**, not a live EXE test. Never copy the source's account numbers, cookies, keys, signed values, raw HTML or HAR into this public repository.

## Newly grounded facts (from the inventory; independently reverify before live use)

| Route / element | What the inventory supports | What it does **not** prove |
|---|---|---|
| GET `/hsjw/cas/login.action` | Login page marked GBK; session cookie scoped to `/hsjw` | Native EXE success, CAPTCHA/SMS/SSO behavior on other visits |
| GET `/hsjw/frame/homepage?method=getTempDeskey` and `getTempNowtime` | Temporary values participate in the signed normal form | Full reproducibility of all dynamic original-JS fields |
| POST `/hsjw/cas/logon.action` | Signed request has `params/token/timestamp/deskey/ssessionid`; business JSON statuses observed | A synthetic crypto test matching the current live form byte-for-byte |
| GET `/hsjw/student/wsxk.zx.html` | Normal S202 main page with `ActionForm` fields | Which round and class rows are available when the window opens |
| POST `/hsjw/taglib/DataTable.jsp?tableId=2568&fre=1` | S202 course-list query posts a form to an HTML table with 17 named headers | A complete validated POST body, actual available courses, or a remaining-seat column |
| POST `/hsjw/STU_DynamicInitDataAction.do` | Selection-page initialization exists | Stable `lcid` and other values for a particular current round |
| POST `/hsjw/jw/common/getWsxkTimeRange.action` | At 23:02 the window was closed and returned business status 400 | Exact open-state semantics or that the user's round is active |
| POST `/hsjw/jw/common/isSelectableSkbjdm.action` | An eligibility/check action was discovered | Current course-specific eligibility and response meanings |
| POST `/hsjw/STU_ElectCourseAction.do` | An apparent election action was found; missing form content gives an empty response | A working/authorized write contract, success/timeout/duplicate handling |
| GET `/hsjw/student/wsxk.zxjg.jsp` | Result page can be navigated to | Final result rows in an active selection round |

The inventory explicitly reports a closed window (`xktime_flag=0`), a closed-window business response, and zero selectable courses. No successful production selection is evidenced.

## Corrections to avoid false readiness

1. The 17 table headers include class number (`head_skbh`), course code (`head_kcdm`), selection status (`head_xk_status`) and coin input (`head_xk_points`). **None names remaining seats**. A generic parser treating one of these columns as `remain` is unsafe.
2. A displayed coin field alone does **not** validate whether the current round is bidding, first-come, or subject to other official rules. Confirm in the official UI and accepted write/readback semantics before changing selection logic.
3. A 200 HTTP transport status can contain a credential-expired filter page. An accepted submit acknowledgement is still not final registration; compare authenticated selected-course records.
4. `getWsxkTimeRange` status 400 was captured while closed. Do not infer all other statuses from this one observation.
5. The existing EXE's signed request builder uses fixed `_u` and `_p` names. The new inventory describes `_u{rand}` and `_p{rand}` variants. This **requires direct comparison to first-party JS at runtime**; do not silently change crypto or hardcode captured one-time parameters.
6. The new result page and the older HAR's selected-course result URL are different routes. Keep them distinct until verified.
7. The inventory's suggested sub-second polling and parallel workers are not evidence of the institution's allowed frequency. Respect official quotas, 403/429, and explicit verification challenges. No CAPTCHA bypass, browser-cookie extraction, or cross-account access.

## This branch

- Fixes the signed-login parser to accept the login HTML page's declared GBK encoding with strict consistency checks. It does not change challenge handling, token construction, or authorization.
- Adds `core.ncwu_elective_table.inspect_elective_table` plus synthetic tests. It only recognizes an exact set of 17 header IDs in a locally supplied HTML table, counts *candidate* rows and never generates an availability, bid, remain or submit decision.
- Adds a `工具与日志` → `选课表头 HTML 核验（离线）` action. It reads at most 2 MiB of local HTML and performs **zero** official-site requests.
- Leaves production enrollment writes and current real catalog polling disabled.

## Remaining acceptance gates, in priority order

1. **Signed login live acceptance**: check the app's actual authorized login and identity readback, with official CAPTCHA/CAS/SMS if prompted. Verify first-party JS field naming and server key/time formats without saving or publishing credentials.
2. **Read-only official window and S202 form**: obtain the actual selection-round form field *names/types* from one's own normal site navigation, including whether forms depend on ephemeral values. Do not store a previous session's `xh`, `lcid`, tokens or cookies in YAML.
3. **Course list / seats**: capture a personal, permitted, sanitized example of table data during an open round; match course code, class number, selection status and any independently verified seat field against official UI. Header-only HTML remains insufficient.
4. **Official readback**: validate the current selected-course record and identity scope, including duplicate and timeout reconciliation.
5. **Submission**: only after published permission and complete positive/negative contract evidence, implement a separately gated school-specific adapter; keep one write per target with persistent pending state and verified final record.
6. **Windows delivery**: rerun all unit tests, PyInstaller, packaged self-test, genuine GUI smoke, and personal live acceptance. None of the current synthetic tests establishes campus success.

For future capture review, provide **redacted field names, HTTP methods, response status and non-sensitive response schema**, not real passwords, cookie values, private class records, identifying IDs or live signatures.
