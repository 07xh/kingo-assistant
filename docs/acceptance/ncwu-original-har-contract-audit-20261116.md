# NCWU capture-grounded contract audit · 2026-11-16

Source provenance: user's own 2026-11-03, 2026-11-07, and 2026-11-14 HAR files, inspected **offline in a private working container**. Only fixed HTTP method/host/path, parameter *names*, response MIME and non-personal schema names appear here. **No original HAR, request values, response values, credential, session, signature, student number or timetable record was added to the repository.** This report does not certify institutional third-party API permission or completed live EXE authentication.

## What the original evidence actually shows

| Known page/action | Observed HTTP contract | Distinction / implementation decision |
|---|---|---|
| School login, early capture | POST `jwmis.ncwu.edu.cn/hsjw/cas/logon.action`; form keys `username`, `password`, `loginmethod`; text/plain JSON response with `status`, `result`, `message` | The values and required auth challenge are not validated. `auth.login.contract_variant=plain_20261103` is a *manual contract choice*, not default. No credential is submitted until official request mapping, identity proof and request policy are verified |
| School login, later capture | Same POST path; **different** form keys `deskey`, `params`, `ssessionid`, `timestamp`, `token` | This is a dynamic/signed-looking variant, not the former plain form. `signed_20261114` explicitly fails closed; no captured token or key may be replayed |
| School current semester | POST `/hsjw/jw/common/showYearTerm.action` with **no form fields** in two independent captures; text/plain containing a JSON object | The JSON included structural keys `xn`, `xqM`, `xqName`, `jwUserYeartermId`, `userCode`. The EXE one-shot term probe checks these keys and returns safe status only. Presence of `userCode` alone is not proof it matches the user's intended account |
| Personal timetable JSON | POST `/hsjw//wap/mycourseschedule.action`; `application/x-www-form-urlencoded` fields `param`, `param2`; response content-type **text/plain** containing timetable JSON | Updated parser to accept this observed MIME **only when** JSON schema validation passes. `param`/`param2` are dynamic/opaque, so the static request form adapter now rejects them instead of replaying old captured values |
| Personal timetable HTML entry | GET `/hsjw/student/xkjg.wdkb.jsp` with `menucode` query key, GBK HTML | The actual query value was not re-used; default contract remains disabled |
| Personal timetable HTML detail | GET `/hsjw/student/wsxk.xskcb10319.jsp` with `params` query key, GBK HTML | Actual query generator not verified; never guess or replay a one-time opaque query |
| Selected course result HTML | GET `/hsjw/wsxk/xkjg.ckdgxsxdkchj_data10319.jsp` with `params` query key, GBK HTML | Not a list of courses still available to choose. Existing strict HTML adapter stays blocked until query requirements are independently verified |

## Exact code changes

- `core.native_login` distinguishes unknown, observed old plain form, and observed later dynamic form. Unknown and dynamic variants never ask the user for a password or send a guessed login packet.
- `core.native_timetable` handles authentic `text/plain` JSON but refuses static `param`/`param2` bodies.
- `core.ncwu_term_probe` implements the observed **one-shot, no-body, same-origin POST**, gated on an EXE-owned school session, a confirmed read contract and independent published request budget. It checks limited JSON string field shapes, does not reveal `userCode`, and never claims confirmed identity/enrollment from HTTP 200.
- Windows GUI **工具与日志** now has `核验学期接口（已抓包 · 只读）`. A second session operation cannot start concurrently with the term probe. It never opens a browser.
- Every published test fixture is synthetic. Windows CI validates safety gates and packaging only, **not campus live availability**.

## Remain to be verified

1. Legitimate EXE-native direct login protocol selection and any additional official challenge, with independently verified post-login account identity. The 2026-11-03 and 11-14 requests are not interchangeable.
2. Whether and when the institution permits automated read-only requests and the appropriate published call limits.
3. An actual EXE session calling the real no-body `showYearTerm.action` and checking its response with the official UI.
4. A sanctioned builder for the dynamic school timetable query; an elective-catalog endpoint, remaining-seat semantics, exact election-window state, and approved enrollment submit/readback contract.
5. Controlled physical Windows acceptance. No optional UI feature or offline fixture should be taken as live success.
