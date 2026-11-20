# S202 formal HTTP acceptance and exact readback boundary (2026-11-20)

## Acceptance status

The Windows implementation has a disabled, single-shot S202 HTTP transport,
an offline-only request/response field-name HAR inspector, and several
separately implemented read-only S202 result pages. **None of these establishes
an authorized successful enrollment transaction.**

| Gate | Evidence available | Required for live acceptance | State |
|---|---|---|---|
| Institution permission / request limits | Design and fail-closed policy checks | Independently documented permission, time window and request budget | Not accepted |
| In-EXE HTTPS login and identity | Signed-login implementation, session handling, scoped read probes | Authorized same-session account verification | Not accepted live |
| Submit HTTP contract | Known S202 path and bounded one-POST transport | First-party form derivation, exact current-round item and option semantics, success/rejection business meanings | Not accepted |
| Internal teaching-class mapping | Local targets and candidate internal class displays | Exact approved mapping of one target to current-session internal class and submit item | Not accepted |
| Official exact readback | Display-only S202 results and strict snapshot validator | Verified official source returning exact course code + internal teaching-class ID for same account/semester/round | Not accepted |
| End-to-end Windows acceptance | Offline unit tests / EXE smoke | One permitted write with a fresh authoritative selected-record read, including failure and timeout behavior | Not accepted |

## New proof-scoped bridge

The new core/ncwu_verified_readback_bridge.py is a **reader integration boundary**.
It has no own HTTP URL, request parser, submission method, or automatic enablement.
A future independently authorized native reader can be injected as an async
read_snapshot(ReadbackScope) function. It must return a genuine
ExactSelectedSnapshot from an approved first-party official source.

Every call to read_enrollments() now has independent checks:

1. Hash-verified, unexpired official_enrollment_verified attestation,
   required before invoking any reader.
2. Fresh EXE identity: account, semester, official origin, internal student,
   academic year/term, selection round and in-memory session.
3. Strict exact internal skbjdm snapshot, not a display page, visible
   class number skbh or HAR candidate.
4. Reviewed parser SHA and result-page path; timestamp within the read.
5. Re-check identity and evidence *after* awaiting the result. Changed session,
   term or round invalidates the entire response.
6. Exceptions and missing data never become empty official sets or success;
   recovery keeps unresolved submission fences and never blindly retries writes.

The bridge structurally satisfies the existing read-only gateway interface.
A future reviewed official reader can feed EnrollmentEngine or reconcile_pending
without altering serial submission and HMAC-journal protections. **No verified
live reader is shipped or wired by this change.** Real submission stays disabled.
A synthetic test attestation never grants actual institutional permission.

## Evidence still required before live binding

- Current-session first-party form origin and documented field derivation,
  especially the target-to-internal-teaching-class mapping.
- One authorized success and one genuine rejected business response,
  with only schema/response semantics published, no credentials.
- Separate authoritative selected-record source with exact internal class IDs;
  verify empty, delayed, failed, revoked and changed-account behavior.
- Official confirmation of permission for programmatic login/query/write,
  and the applicable rate budget and selection window.

These require authorized first-party observation or institution-provided
documentation. Historical HTML, synthetic success flags, CI and HTTP 200
cannot supply them. Do not send speculative selection POSTs, bypass limits
or avoid authentication challenges.

## Offline test scope

Synthetic tests cover: attested exact records, verified empty snapshot, absent
attestation, display-only rows, wrong source/class/semester, stale or future
timestamps, changed round/session, attestation file tampering during read,
reader timeouts, and no submit API on the bridge.

The tests validate defensive behavior only, not the existence of a working
campus results contract or any real successful enrollment.
