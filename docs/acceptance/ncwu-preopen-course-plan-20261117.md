# NCWU course-plan 5327042 native one-shot read — 2026-11-17

## First-party provenance and distinction

This implementation reuses the observed API schema from the owner's
`07xh/kingo-live/ncwu/hs_kcbcx.py`, recorded as an authorized read-only
NCWU course-plan investigation on 2026-11-13:

- `POST /hsjw/taglib/DataTable.jsp?tableId=5327042&clientWidth=1210`
- GBK URL-encoded filter fields including `xn`, `xq`, `nj`, `pycc`,
  `dwh`, `zydm`, `sel_*`, `xnxq` and the `S2020103` menu code
- HTML `trN_<field>` labels and `showTotalRecord('5327042', 'N')`
- `qdrs` is the explicitly server-reported *plan* available seats field;
  never compute it from `xkrssx - xkrs`.

**Do not confuse this API with `tableId=2568`**, the separate S202
current selection catalog. A historical plan count or earlier vacant-seat
value is neither a live-window availability claim nor an enrollment result.
The 2026-11-13 path has *not* been personally re-accepted against the
current school session as part of this CI.

## Windows integration

- New `core/ncwu_course_plan.py`: at most **three serialized read calls**
  under the native logged-in EXE session:
  1. Get current account/term scope through the existing
     `getWsxkTimeRange.action?xktype=2` first-party API.
  2. GET the **current** `student/wsxk.kcbcx10319.html?menucode=S2020103`
     and read its actual `ActionForm`, including the school's current
     `pycc` value.
  3. Only when the live page and scoped year/term/major/college fields
     agree, submit the known GBK plan filter to 5327042.
- There are **no hardcoded personal identifiers**, fixed department
  codes or copied last-semester credentials. If the page lacks the expected
  form or the server changes it, the EXE returns `form_unavailable`
  without sending any speculative catalog request.
- The parser merges old first-party O/E continuation rows; requires a
  total marker (especially for explicit empty lists); treats malformed
  `qdrs` as unknown; preserves actual course/class code and teacher/time.
  No course or class is invented when records are absent.
- One new `工具与日志` button:
  `读取本学期开课计划（只读快照）` with an in-memory table.
- EXE session, 60-second local cooldown, 403/429/redirect/login-expiry
  stop behavior and optionally verified shared request budget are reused.
  The query does not open a browser, submit/cancel enrollment, poll
  automatically or change the existing official login protocol.

## Evidence/acceptance gates

- Unit tests: GBK Chinese field encoding; closed-window metadata still
  permits a *plan* read when current round context exists; exact host,
  correct three-request order, session-only identity, current semester
  mismatch, missing `pycc`, malformed/missing seats, O/E continuations,
  HTTP 302/429 and invalid/expired login.
- Windows packaged EXE, source self-test and GUI smoke must pass on the
  commit being merged.
- To verify on the user's real account, log in via the EXE, inspect the
  plan snapshot alongside the official school's `开课计划查询` menu and
  confirm the school has published that term's plan. Do not interpret
  zero results as a confirmed empty live selection list. No credentials or
  private HAR files should be published to GitHub.
- `STU_DynamicInitDataAction.do` and
  `isSelectableSkbjdm.action` still lack complete current-form request and
  authoritative outcome evidence. The previously added local HAR contract
  inspector is the next evidence step; these actions have **not been
  silently enabled** by the plan reader.
