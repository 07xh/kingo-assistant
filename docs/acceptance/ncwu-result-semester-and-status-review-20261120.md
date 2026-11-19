# NCWU selected results: local semester + observed status diagnostics

## Evidence observed offline

The owner's authorized 2026-11-03 result page includes selected-course
table rows with explicit `kcdm`, `skbjdm`, `xk_status_m` and
`xtxh` named cells. All sampled result rows had visible status
`选中` paired with an `xk_status_m` value of `03`. This is
**observed markup**, not proof that a new enrollment submission succeeded.

Both 2026-11-03 and 2026-11-14 official `showYearTerm.action`
responses used `xn=2026`, `xqM=0` and
`xqName=第一学期`. No student numbers or raw HAR bodies are committed.

## Added fail-closed semester check

The previous read path validated the student's login identity but did not
compare the returned school term against the locally configured course
plan. Dynamic result reads now require `xnxq=YYYY-YYYY-1` with
consecutive years and an independently checked official first-term
mapping. A mismatch of academic year, term code or term name aborts
**before** the results GET. The second-semester encoding is not inferred
from a first-semester sample and is **not enabled** by this change.

Only the EXE's current verified login account is used; no browser or
captured query value is imported. The source still observes a separate
school-accepted request budget for each first-party read.

## Read-only candidate-to-preference review

The historical results parser now optionally captures `xk_status_m`
beside the displayed status without treating `03` as confirmed success.

The Windows candidate view compares exact `course_id` and internal
`class_id` against the local preferences. It separately indicates:
- an exact pair with the observed historical `03` / `选中` combo;
- the same course with a **different internal class**;
- multiple class candidates for one course;
- a missing candidate;
- incomplete or unrecognized status metadata;
- missing current account/semester binding or an invalid local preference.

The normal desktop status/message cannot expose raw account values;
the per-course table is shown only inside the explicitly requested
read-only popup. An exact candidate match **never** produces an
`ExactSelectedSnapshot` and never changes `AttemptJournal`.

## Still blocked

The legacy results page is not a verified record of the *current S202
selection round*. Its displayed state code has not been independently
accepted as an authoritative write confirmation or mapped to a final
selected-record endpoint. No official write form provider or permitted
write contract has been added. Automatic registration remains disabled.

Offline tests use fictional student/course IDs and HTTP mocks. They
validate the first-term check, network aborts on mismatch, status-field
ambiguity and GUI presentation. Passing them is not live university
acceptance.
