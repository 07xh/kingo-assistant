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
