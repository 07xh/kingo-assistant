# S202 live interface integration: one-shot read pathway

This stage wires the school site's observed S202 query flow into the Windows EXE without browser automation. The uploaded November 16 inventory came from a live school-session inspection: it supports the official paths and query-form mechanics, but does not include a complete active-window write request or known remaining-seat field.

## Implemented
- Reuses the **EXE's own** logged-in, account-verified JSESSIONID, scoped to jwmis.ncwu.edu.cn and /hsjw. Nothing is taken from Edge or other processes.
- Directly GETs `/hsjw/student/wsxk.zx.html`, reads the current `ActionForm` with fresh school-supplied hidden fields, and requires recognized current-round context (xktype=2, xn, xq).
- POSTs `/hsjw/jw/common/getWsxkTimeRange.action` and maps the specifically observed 400 closed-window response to CLOSED; any unrecognized response remains UNKNOWN.
- POSTs that fresh form (never a hardcoded student ID, round ID, or old signature) to `/hsjw/taglib/DataTable.jsp?tableId=2568&fre=1`.
- Validates the 17 first-party S202 table header IDs and parses course code, class number, teacher, selection-status text and selection-coins text into an in-memory live result window.
- If the current selection window is closed but the page and form are available, still queries once and correctly reports zero rows.
- Adds an explicit `实时读取可选课程（S202 · 只读）` button to the existing Windows home screen. One click sends at most three sequential requests; no timer or concurrent workers. A local 60-second cooldown prevents accidental repeated clicks. 403/429 and login-expired HTML immediately stop, without retries.
- Keeps identity, credentials and live response bodies out of logs and GitHub. The live table resides only in process memory.

## Known unsupported contracts

The inventory names multiple other interfaces, **not all of them have complete request-body or response contracts**. These are not secretly enabled:
- `STU_DynamicInitDataAction.do`: body and session initialization side effects need comparison with the current real page.
- `isSelectableSkbjdm.action`, `getSelectLessonScoreKcsInfo.action`: form fields/results need a current authorized read-only sample.
- `STU_ElectCourseAction.do`, `cancelElectiveCourse.action`, `STU_ElectCourseResultAction.do`: these change registration and are not invoked until written protocol and final official result reconciliation are tested.
- QR login, password change and menu writes are distinct workflows, not prerequisites for course querying.

**Do not equate this bounded real-query implementation with a successful live-account acceptance test.** The current site could render the page through AJAX or fill ActionForm after JavaScript has run; without the site's actual page/response samples the native parser can correctly return `form_incomplete` or `table_unrecognized`. In that case confirm the real origin of the form fields in a sanitized capture rather than guessing them. Automatic background polling and any enrollment writes remain off until school permission/rate and write contracts are validated.

## Verification
- Unit tests: synthetic httpx MockTransport requests assert same-origin, correct Referer, fresh form, 3-request order, closed-window handling, GBK HTML, no reserved-seats field, and fail-closed redirects/429/expired session.
- Windows CI must build PyInstaller and run packed Tk GUI smoke before merge.
- Separate personal Windows check: log in and click the new S202 button. Confirm exact courses/teaching classes on the official site and report whether it returns `catalog_read` or a contract error, without exporting personal data.
