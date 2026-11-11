# V2 Offline Enrollment FSM — Test and Integration Contract

This change refactors the existing `clients/windows/source/core/enrollment.py`.
It **does not** add a real NCWU adapter, any school endpoint, or live enrollment writes.

- Immutable `CourseTarget`, `CourseOffering`, `TimeSlot`, and `ScheduleSnapshot` use exact IDs, semester checks, and explicitly known week sets. Unknown schedule data blocks the write path.
- `EnrollmentFSM` uses a closed transition table and immutable redacted event entries. Report events are not raw HTTP records.
- `AttemptJournal` writes a PENDING intent fence before submitting, supports old V1 attempt-only files, and persists CONFIRMED or REJECTED outcomes. Hashes are deterministic, **not salted**.
- `VERIFYING -> VERIFY_WAIT -> VERIFYING` permits bounded *read-only* checks after delay. A timeout or a stale read cannot trigger another submission.
- Restart reconciles PENDING entries via authoritative read-only records; if unresolved, it remains blocked. A stale lock after a crash requires manual safe reconciliation.
- The existing scheduler, HTTP client and Windows UI remain read-only. A local fake gateway claiming `official_write_verified` is only a test fixture, not a trusted real adapter.

Tests cover every declared and disallowed state transition, immutable nested models, timetable parity, missing data, ambiguous submissions, timeout with later confirmation, delayed record visibility, legacy journal recovery, duplicate-write suppression, and explicit rejection gates.

Release gate: offline CI passing does **not** establish NCWU course-catalog or write integration. The full adapter contract, institutional permission and official confirmation must be separately verified before any live use.
