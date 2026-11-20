# 2026-11-20 · Legacy NCWU selected-results internal-ID candidates

## Grounding

Offline structural inspection of the owner's authorized November 3 NCWU HAR
identified a course-result HTML table returned by the **existing** known
first-party read-only endpoint:

`GET /hsjw/wsxk/xkjg.ckdgxsxdkchj_data10319.jsp`.

Its visible table has course, class, and status columns; its course rows
contain paired `<td name="kcdm">` and `<td name="skbjdm">` cells.
The saved capture also showed an opaque `params` query parameter; its
real value is **not** in this source and must not be guessed.

## Implementation

`inspect_known_result_candidates` only parses bounded in-memory HTML.
It requires the same table to show `课程`, `选课状态`, and
`上课班级代码` headings, and each accepted row to have exactly one
`kcdm` and one `skbjdm` named cell. Ambiguous/missing/invalid rows
are omitted. The result may be displayed by the Windows **已选课程结果网页**
action if (and only if) its independent current-session read-only request
contract and queryless request have already been verified. The existing
request-budget and authorization gates are unchanged.

This improves source observability without conflating the **presence of
internal-ID field names** with an officially confirmed enrollment. The
live account, semester, selection round, server-side registration status
and authorization evidence still require separate validation, so these
candidates can **never** feed `EnrollmentEngine.read_enrollments()` or
issue a gate attestation.

No student number, Cookie, query-token value, course identifier from the
real HAR, or raw HTML is committed. All regression fixtures are invented.
