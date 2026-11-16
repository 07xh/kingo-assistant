# NCWU S202 GBK catalog and round preflight · 2026-11-17

## Sources / evidence limits

- The owner's 2026-11-16 first-party inventory establishes the S202 `ActionForm`, `DataTable.jsp?tableId=2568&fre=1`, the 17 captured header IDs, the exact `getWsxkTimeRange.action` path and a closed-window response.
- The independent `07xh/kingo-live` repository contains earlier first-party read-only `getWsxkTimeRange.action?xktype=N` and legacy Kingo DataTable implementations; these confirm that a live selection form may be initialized from server-returned context. They do **not** establish that the older preselection `tableId=2548` or static plan `5327042` are equivalent to S202's table `2568`.
- Earlier NCWU study confirms GBK form encoding matters: a UTF-8-encoded Chinese filter may silently return zero rows. The fresh 2568 form is still obtained from the EXE's *own* session, never from a captured account.
- No completed official selectable-class validation, S202 submission or exact selected-class confirmation has been observed on this Windows build.

## Implementation

1. `core/ncwu_live_elective.py` encodes the current real S202 POST form with the already-existing `core.client.encode_gbk_form`, using `Content-Type: application/x-www-form-urlencoded; charset=GBK`; it still sends only GET/POST to the previously identified read paths and does not guess empty form fields.
2. The 2568 HTML parser now derives column positions from the current header IDs, accepting both positional 17-column rows and `trN_<field>` labelled cells. It skips continuation/incomplete rows, detects duplicate keys, does not derive `skbjdm` or `remain` from display labels, and does not promote selection/status/coin text to a validated seat count.
3. New `core/ncwu_s202_round.py` performs **one read-only, same-session POST** to `getWsxkTimeRange.action?xktype=2`, showing the current round label, term, reported start/end and a count of six expected context fields. For closed windows the official response may still contain round metadata; this remains diagnostic only and is never used to populate a form or unlock enrollment.
4. Windows `工具与日志` adds `核对当前 S202 轮次（只读）`. The user's session is verified by the existing login module. New round probing has a 60-second local cooldown and honors an explicitly verified shared server request budget where available. 302/401/403/429 and HTTP 200 expired-session responses fail closed, without retries.
5. All updates preserve the existing software-owned login, S202 course read, enrolled-result readback, and older HAR diagnostics.

## Outstanding integration gates

- `STU_DynamicInitDataAction.do` appears by name in the user's inventory but has **no verified request body or response contract**. The website's own `getWsxkTimeRange` response provides enough confirmed read-only metadata for current-round diagnostics; this is **not** evidence that the dynamic-init action itself has been called.
- `isSelectableSkbjdm.action` is identified but lacks tested param names, authoritative class identity and negative/positive response schemas. Do not send guessed requests or equate `head_skbh` (display class number) to `skbjdm` (teaching-class identifier).
- Enrollment submit and course withdrawal are separate state-changing actions; **both remain disabled** until reviewed permission and a complete authorized write/response/readback contract exist.
- Source-level CI and packaged Windows Tk GUI smoke tests are necessary but never substitute for the user's live-account test. The newest first-party HTML could use JS-populated forms or revised field naming. A rejection like `form_incomplete` or `table_unrecognized` is a safe result, not permission to forge values.

## Manual verification sequence

1. Run the built EXE, perform one official software-owned login and confirm identity readback.
2. Click `核对当前 S202 轮次（只读）` once; record only the non-sensitive status code, round name and whether it reports closed/open/unknown. Do not share session IDs or private context values.
3. Click `实时读取可选课程（S202 · 只读）`. Compare returned course names, class numbers and selection statuses to the official site. If still closed and the school does not populate its form, the response may legitimately be `window_closed`.
4. In a later permitted selection window, validate the exact eligibility request and official result readback separately. Until then no automatic write action can be described as working.
