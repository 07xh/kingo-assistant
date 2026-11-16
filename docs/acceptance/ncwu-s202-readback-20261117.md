# 2026-11-17 S202 selected-results readback and pre-release checks

The owner's first-party November 16 interface inventory identifies
`GET /hsjw/student/wsxk.zxjg.jsp` as the official positive-selection
results page. It does **not** include the complete result-table HTML schema,
a successful selection transaction or a verified write contract.

## Added to EXE

- `core/ncwu_s202_readback.py`: one in-process, authenticated, same-origin
  GET using the EXE's current session cookie; does not import browser state.
- The result page is decoded strictly with the declared HTML encoding
  (including GBK). HTTP 200 credential-expired pages, redirects and
  access restrictions are rejected. 429 is never automatically retried.
- Optional verified shared request budget is honored; all user-triggered
  reads have a 60-second local cooldown.
- The parser counts result tables and recognizes **candidate records only**
  if headings clearly identify both a course code and teaching-class number.
  Any remaining fields are optional. No inferred record is marked success.
  Empty or unrecognized tables are **not** treated as zero selected courses.
- Windows `工具与日志` adds a `读取华水 S202 已选结果（只读）` entry,
  with an optional memory-only candidate-record window. It does not contact
  campus endpoints without a verified EXE session.
- Previous HAR-based selected-results inspection remains separate;
  this new route is the November 16 S202 page, not the older dynamic
  `/hsjw/wsxk/xkjg.ckdgxsxdkchj_data10319.jsp` endpoint.

## What remains blocked

- `STU_DynamicInitDataAction.do` and `isSelectableSkbjdm.action` still
  lack validated first-party request-body fields and success/failure samples.
  A pathname alone is not sufficient to send guessed parameters.
- `STU_ElectCourseAction.do`, course withdrawal and the current bid/seat
  mechanics require an explicitly permitted, verified write transaction
  and official selected-record confirmation before implementation as
  production actions. No write POST or high-frequency polling is enabled.
- No offline or CI test constitutes real school account acceptance.
  The current user can inspect the one-shot S202 response in the Windows
  EXE and compare any candidate rows with the official selected-course UI
  without exporting student records.

## CI and release gates

Run all Python tests and source self-test, PyInstaller, the packaged
`--self-test` and real Tk GUI smoke test. Test-budget timing measures
the cooldown from its start, to avoid a false negative on slow runners
where SQLite re-open consumes a material part of a short synthetic wait.
Do not weaken the actual persisted `Retry-After` rate limiter.
