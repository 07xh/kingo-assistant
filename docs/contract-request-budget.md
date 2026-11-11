# Contract request budget (prototype)

This module is intentionally **not** proof of a school's rate limits or of permission to send any request. School integration remains disabled until independently validated and explicitly authorized.

## Components

- WindowLimit and RequestPolicy: immutable contract with finite, positive multi-window quotas. verified=False rejects budgeted requests.
- GlobalRequestBudget: SQLite-backed transactional sliding-log reservation. Multiple processes using the same database share request counts and a persisted Retry-After cooldown. Every attempted send consumes one reservation even on cancellation. Clock rollback fails closed.
- SingleFlight: coalesces only simultaneous identical read-only catalog calls. Different queries and later calls obtain fresh results.

KingoClient.fetch_course_list() now uses in-flight single-flight coalescing; the school endpoint, polling interval and credential handling are unchanged.

## Important integration limits

1. The quota manager is a reusable **offline-tested component**, not yet wired into every network call. All school-origin methods (identity, entry, catalog, enrollment verification, any future write adapter and auxiliary time sync) must eventually pass one verified per-origin budget gateway.
2. SQLite transactionality ensures quota reservations across processes that share the same database. **Concurrency is currently per manager instance**, not globally enforced across processes. Use an OS-enforced single-run lock or a broker before enabling multi-process network traffic.
3. Wall-clock time is used for persistence and is not an authoritative server clock. Clock rollback is rejected, but forward jumps, filesystem tampering, or another machine using another database can defeat client-side accounting. Treat official access policies as authoritative.
4. RequestPolicy.verified=True is a software assertion, not independent evidence that a published policy exists. Do not infer permission from configuration alone.
5. There is no school-specific mutation adapter. The existing enrollment journal remains authoritative for write uncertainty; a missing selected-course record is never sufficient justification to automatically resubmit a non-idempotent write.
6. Budget checks add small local processing overhead. They improve efficiency and reliability, not the server's allowed throughput.

## Offline tests

From clients/windows/source, run:

python -m unittest discover -s tests -q

Tests cover multi-window enforcement, shared DB accounting, cooldown after restart, concurrency serialization, policy mismatch and duplicate read coalescing. They use synthetic data only.
