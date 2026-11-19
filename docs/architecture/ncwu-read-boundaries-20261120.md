# NCWU async read boundary and gate diagnostics (2026-11-20)

The NCWU S202 gateway now checks the same-session identity, academic year,
term and selection round, capability flag and recorded attestation before and
after every awaited read of the official window, catalog, timetable or selected
classes. An account switch, changed round, expired evidence or revoked
capability invalidates the returned data. Tests are offline and do not access
the school network.

Windows Home now offers a read-only "验收详情" dialog listing all four local
evidence gates. It never issues an attestation, configures an enrollment
adapter, performs a school request or enables the disabled submit button.

The existing S202 results-page parser emits only candidate rows, not
authoritative enrollment records. It must not be used for the engine's
`read_enrollments` capability. The source is still required to provide an
independently validated exact teaching-class record, a complete current-session
form provider and the proven course-to-items binding.

This patch does not implement those missing institutional contracts and does
not make automatic enrollment operational. Final acceptance requires approved
real-interface tests and official record readback.

## Offline submission evidence inspection

The Tools page includes a local-only S202 submit-HAR inspector. It inventories
exact HTTPS POST records for the known first-party submit path and GET counts
for the selected-results page. The report includes only bounded field names,
HTTP status codes, request formats and response shapes; no values or HAR bytes
are retained or uploaded. A missing form field is a **contract discrepancy
to investigate**, not a guessed field to send. A captured submit POST and
HTTP 200 are not proof of enrollment; no form provider or write permission is
created from the report. Synthetic tests cover secret redaction, spoofed
hosts, wrong methods and missing samples.
