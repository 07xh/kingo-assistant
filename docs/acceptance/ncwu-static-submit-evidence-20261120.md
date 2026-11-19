# NCWU S202 static submit evidence — local HTML/JS inspection (2026-11-20)

## Scope

KingoAssistant already has an observed first-party S202 submit route,
\`/hsjw/STU_ElectCourseAction.do\`, and a separate local HAR submission
inspector. The main missing information is the successful same-session
\`items\` source and exact internal teaching-class binding. Path guessing is
not a substitute for that evidence.

This change adds **核对提交 HTML／JS（离线）** to the Windows **工具与日志**
page. The user explicitly selects saved \`.html\`, \`.htm\` or \`.js\`
files from their own permitted page navigation. The new
\`core/ncwu_static_submit_evidence.py\` extracts only:

- Number of saved sources with a text reference to the **known** submit path.
- Number of HTML \`POST\` forms whose action exactly matches that path
  (either a same-origin HTTPS absolute URL or an absolute path).
- Field **names** in those matching forms, plus which of the seven
  existing transport-expected field names were not observed.

The report has four non-verified states:
\`no_static_reference\`, \`text_reference_only\`,
\`partial_form_observed\`, \`form_fields_observed\`. None means an endpoint,
write permission, dynamic parameter, account binding or enrollment outcome
is verified. Text references can exist only in comments or inactive JS.

The parser caps file count and size, rejects unsupported extensions, avoids
following symbolic links from the UI, and handles UTF-8 / GB18030 saved source.
No credential value, raw source, file path or network response is returned to
the interface. The user can copy only the derived report via the existing
**复制脱敏契约报告** button. All raw bytes are discarded after inspection.

## Explicit boundaries

- No unknown-endpoint enumeration, automatic GET/OPTIONS scanner or synthetic
  POST. Even a small POST count can cause side effects.
- No cookie import, no \`WarmSession\` mutation, no startup network activity.
- No live request-template caching, pre-serialized POST body or write toggle.
- No automatic attestation of the official-window, catalog, enrollment or
  submit capabilities. Existing enrollment gates remain unchanged.
- The existing HAR inspector remains the preferred proof of an **observed
  request**, not proof of successful enrollment.

## Tests

The unit tests cover exact same-origin forms, unrelated external forms,
GET-vs-POST discrimination, comments containing path text, value redaction,
relative paths and invalid/oversize input. The Windows page wiring test also
verifies that constructing the Tools page does not invoke the action.

No school network request is necessary for testing.
