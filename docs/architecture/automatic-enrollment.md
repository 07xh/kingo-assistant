# Automatic enrollment workflow and acceptance gates

## Current implementation status

The Windows V5.1 mainline includes a hardened fail-closed automatic-enrollment state machine, editable priority order, and persistent attempt-state transitions, but **there is no NCWU live enrollment adapter**. The client remains read-only. Offline tests prove only the orchestration logic against synthetic data.

The 2026-11-11 HAR audit confirms timetable JSON and Xiqueer selection-related metadata, but not a verified elective catalog, live remaining-seat field, or authorized submission/confirmation contract. One response explicitly reports that the current time is outside a valid selection window. Do not map timetable or `zxinfo.zxxq` records into available elective courses or seats.

## Execution state machine

1. Validate a strict ordered list of exact course + teaching-class identities.
2. Require explicit user opt-in, school permission, and four independently reviewed adapter capabilities: official catalog, current enrollment records, selection-window state, and write contract.
3. Read the official selection-window state. Only `OPEN` may proceed; `CLOSED` and `UNKNOWN` stop before catalog or write work.
4. Read official enrollments before acting. Existing enrollments suppress duplicate writes.
5. Read a uniquely matching offering with a validated remaining-seat field. Unknown, missing, duplicate, or zero-seat entries are not submitted.
6. Re-check the official selection window immediately before each write.
7. Persist a pending intent, then issue at most one serial submission for that exact teaching class.
8. Re-read official enrollments. Only the official record confirms success; a transport acknowledgement does not.
9. An explicit rejection may advance to the next preference after refreshing the catalog. A pending/ambiguous result stops and must be reconciled against official records before any retry.
10. A previously rejected target is never resubmitted automatically. A previously confirmed target missing from the current official record is a discrepancy and stops the run.

## Local journal

- Version 2 uses a per-journal random HMAC salt and a non-PII account scope supplied transiently by the adapter. The journal stores only fingerprints and `pending/rejected/confirmed` states.
- Version 1 fingerprints migrate to `legacy_pending` and require reconciliation.
- The process lock uses an OS file lock. The lock file intentionally remains after normal exit; an orphaned file is reusable once the OS releases the lock.
- No account identifier, course ID, class ID, cookie, token, response body, or raw HAR is persisted in the journal.

## UI priority configuration

The course editor's list order is the preference order: top to bottom. Courses with the same non-empty alternative group are mutually exclusive alternatives. The editor must provide explicit move-up/move-down actions and preserve order in saved plans.

## Required live acceptance before activation

- Verify official permission and current published request limits.
- Verify real course catalog and seat fields against the official UI during a valid selection window.
- Verify the official window indicator, exact enrollment transaction contract, and final enrollment-list readback.
- Verify login/session reuse through an approved authorization flow; never publish raw HAR credentials or signatures.
- Implement a school-specific adapter with fixed allowlisted hosts and paths, no arbitrary URL from YAML, no CAPTCHA or rate-limit bypass, no concurrent write bursts, and immediate stop on 403/429 or unknown response.
- Run a controlled end-to-end acceptance using a low-impact test selection or another institution-approved test procedure before enabling production writes.

Until every gate passes, no production write adapter is constructed or activated.
