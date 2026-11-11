# NCWU official selection schedule and live verification gates — 2026-11-11

Status: **public schedule confirmed; personal live catalog and window remain unverified**.

## Public official evidence

- [NCWU teaching-work announcement, published 2026-10-29](https://www.ncwu.edu.cn/info/1063/23332.htm):
  - **2026-11-09 through 2026-11-13**: professional elective preselection (专业选修课预选).
  - **2026-12-21 through 2026-12-25**: public electives and physical education selection (公选课/体育选课).
  - The document is an institution-wide work schedule, **not an authenticated student-specific window response**. It does not establish the exact opening clock time, campus, student eligibility, or available teaching classes.
- [NCWU first-term online selection notice, published 2026-09-17](https://www2.ncwu.edu.cn/jiaowu/info/1008/2973.htm): documents the direct academic web login, unified campus login and Xiqueer mobile app. These are login routes, not proof of independent catalog backends, separate quota pools, or alternate enrollment channels.
- [Sanitized 2026-11-11 Xiqueer HAR audit](ncwu-xiqueer-har-2026-11-11.md): contains a timetable JSON response and selection-related metadata. One response reports a closed selection window. No authenticated stable elective catalog, validated seat counts, or live submit contract was established.

## Separate evidence dimensions

| Gate | Status | What still needs to be observed |
|---|---|---|
| Published institutional date ranges | Confirmed | Exact course/grade/campus announcement for the user's enrollment round |
| User-authenticated session | Unverified in KingoAssistant | A supported login/session reuse flow, without exporting credentials |
| Official selection-window current state | Unverified | A **fresh**, authenticated official state indicator for the matching account and selection type |
| Elective catalog and exact teaching class | Unverified | Official UI and authorized read-only response with independently matched IDs |
| Available-seat count | Unverified | Exact semantics of seats remaining; never infer from capacity, 200 or timetable data |
| Previously selected records | Unverified | Readback from the official student enrollment list |
| Write and confirmation contract | Not implemented | Institution-approved interface and bounded end-to-end acceptance |

A general calendar date is **never** sufficient to mark `WindowState.OPEN`.
An HTTP 200, public login response, or timetable is **never** sufficient to mark `CatalogState.VERIFIED_OK`.
Do not guess absent endpoints, replay HAR signatures, poll without approved policy, or enable write operations before all independent gates pass.
