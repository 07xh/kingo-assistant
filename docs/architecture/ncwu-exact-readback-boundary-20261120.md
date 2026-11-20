# Exact NCWU S202 official selected-class readback boundary (2026-11-20)

## Reason for the contract change

The S202 display result page observed in the owner's historical HAR exposes
visible course titles and `上课班级` (display teaching-class strings).
Neither a displayed row nor an HTTP 200 includes an independently verified
`skbjdm` (internal teaching-class ID), so neither may mark a target selected.

Previously, the NCWU submission gateway could pass a source's bare
`set[(course_id, class_id)]` directly to the engine once static adapter flags
and hashes were present. A future misconfigured or cached parser could therefore
emit an incorrect set without any **result-specific scope**.

## New contract

`core.ncwu_exact_readback.ExactSelectedSnapshot` is an explicit typed
proof-envelope. Only a separately validated first-party source can create one
after an actual account/session-bound **fresh** official read. It records:

- account scope, semester, internal student identifier, academic year/term,
  selection round and official source origin/path;
- explicit `identity_field="skbjdm"`; the display-only `skbh` is rejected;
- an immutable set of exact (course code, internal class ID) pairs;
- the digest of the reviewed official-results parsing contract;
- the result parsing time from `time.monotonic()`.

`NCWUEvidenceBoundGateway.read_enrollments()` checks that each element matches
the active account and selection round, the digest matches the **current**
hash-verified `official_enrollment_verified` attestation, and the snapshot
was produced during the **current** awaited read. Bare sets, display candidates,
stale cached values, mismatched rounds and invalid identities fail closed.

A valid empty exact snapshot is different from a failed read, so the engine
never interprets failure as an empty selected-course list. No plaintext
credentials, hashes of sessions, raw server records or original HAR data are
included in this document or the tests.

## Remaining blockers

This change does **not** implement the missing university-authorized exact
readback parser, the write form provider, school permission, or submit
activation. It is a stricter seam for future real integration and offline
recovery. The Windows Auto Submit button remains disabled until independent
live acceptance and all four attestations are satisfied.
