# Read-only recovery of ambiguous enrollment attempts

The existing `EnrollmentEngine.run()` requires the officially verified
enrollment window to be OPEN before it can consider any write. This is correct
for enrolling but can prevent the *read-only reconciliation* of an old,
ambiguous submission after the selection window closes.

KingoAssistant V2 adds `EnrollmentEngine.reconcile_pending(targets)` as a
separate, **one-request read-only operation**.

## Contract

- Requires a separately verified `official_enrollment_verified` readback
  capability, exact matching `account_scope` and `enrollment_semester`.
- Does not require write consent, the verified write adapter, or an OPEN
  enrollment window. It does **not** test the enrollment window.
- Checks an official enrolled-course record once. No retries, parallelism,
  CAPTCHA handling, or school-specific endpoints are added.
- Keeps the V5.1 process lock and HMAC attempt journal, including legacy V1
  migration. It does not reset an unresolved fence after a negative read.
- Only positive authoritative selected-course records can move
  `pending/legacy_pending/rejected -> confirmed`.
- A missing record leaves `pending` untouched; a previously confirmed
  record missing now produces `CONFIRMED_MISMATCH`.
- Returns typed, immutable `RecoveryEntry` objects with local target keys,
  `RecoveryStatus`, `unlisted_pending` count and
  `official_read_ok`. No cookies, account ID, raw HTTP, or course codes
  are recorded in the result.
- If a pending HMAC fingerprint is no longer present in the user's list,
  it is surfaced as `unlisted_pending`; the tool never infers or deletes
  that record to make future submissions possible.

## Integration boundary

The Windows GUI is still read-only; no live NCWU `read_enrollments` adapter
is available. This method is ready for a future verified read-only UI action,
but fake-gateway tests **do not constitute live validation**.

The action may update the local HMAC journal to `confirmed` after a valid
readback. “Read-only” always refers to the **university service**: there
are no course-selection writes, only local persistence of evidence.

## Tests

`tests/test_enrollment_recovery.py` covers selection window CLOSED,
no write opt-in, mismatched account/semester, read failure, corrupt JSON,
concurrency locking, pending preservation, HMAC migration, rejected/confirmed
reconciliation, unlisted records, and no school write calls. Existing Windows
tests remain required before merging.
