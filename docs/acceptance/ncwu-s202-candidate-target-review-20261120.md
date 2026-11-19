# S202 selected-results candidate review (2026-11-20)

S202 GET `/hsjw/student/wsxk.zxjg.jsp` returns a page whose complete
result-row schema and teaching-class identity mapping remain unverified.
The existing parser returns **candidate** rows only.

This change compares those candidates to saved local preferences by exact
course code and optional course name/teacher. It reports one candidate,
multiple candidate rows, missing course, conflicting metadata, unknown
metadata and invalid target distinctly. The Windows result popup displays
the report and marks *only* matching visible rows as **课程候选**.
The user's configured internal class ID is **never** inferred from the
page's visible teaching-class number `skbh`.

No school request, write adapter, automatic submission, verified
`read_enrollments()` source or attestation is created. A successful
candidate match cannot make the engine mark enrollment success, and an
empty page cannot prove the student has no selected courses.
Offline tests cover exact matching, duplicate/ambiguous rows, metadata
and invalid config, plus Tk callback/widget data flow.

Production authoritative readback still requires a verified personal
account+semester+internal-class record contract and separate acceptance.

## Historical 2026-11-14 HAR, structural-only acceptance

The owner's earlier NCWU HAR was inspected locally, without uploading it to
the public repository or retaining request/response values in this patch.
The observed S202 result-page HTML contains a direct `thead` with `td`
display headers such as `课程` and `上课班级`, not the strict
`课程代码` and independently confirmed internal class ID required for
authoritative readback.

The parser now reports a separate count of such **display-only tables**.
Nested navigation tables are excluded from double counting. When there are
no trustworthy candidate rows, the Windows UI explains the schema gap instead
of implying there are no selected courses. No display-only table is converted
into `EnrollmentEngine.read_enrollments()` records, and no private HAR data
is committed. Synthetic HTML fixtures reproduce the table structure only.

## Display-only course viewer

Historical evidence also contains nonempty rows in the S202 results table,
with `课程`, `任课教师`, and `上课班级` display columns. The
readback parser now returns these rows as `S202DisplayCourse` objects:
course name, teacher, displayed class and optional displayed class name only.
Windows renders the rows in a separate clearly labelled **display-only**
view when no trustworthy course-code/class-number candidates exist.

This preserves the user's ability to inspect their own course data without
claiming an internal class identifier, verified semester, successful submit
or enrollment completion. The engine still rejects these display records as
authoritative readback, and no private HAR response is committed.
