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
