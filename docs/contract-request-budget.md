# Contract-based request budget (read-only integration)

This module does **not** prove that a university permits a request rate. The live client now fails closed when the independently verified server policy is absent rather than relying on a guessed polling interval.

## Components and integration

- WindowLimit and RequestPolicy: immutable multiple-window quotas with a required source and matching HTTPS origin.
- GlobalRequestBudget: local SQLite transactional sliding-log reservations, persisted Retry-After cooldown and OS-backed cross-process single-request lease. Cooperating clients using the SAME local DB share both quotas and request exclusivity.
- SingleFlight: coalesces only simultaneous identical catalog reads; no stale response cache.
- KingoClient.read(): every physical attempt (including safe-read retries) reserves a slot. A 429/503 Retry-After is persisted. 403/429 immediately stop.
- Identity verification, entry-page reads, catalog monitoring and school-origin HTTP Date fallback use the budgeted read method.
- Windows readiness and CLI live startup reject missing verified policy. Offline-only tests explicitly use MockTransport-only mode.

## Configuration (disabled template; NOT an official school policy)

server_policy:
  verified: false
  source: ""
  origin: "https://jwmis.ncwu.edu.cn"
  windows: []
  max_concurrency: 1
  database: "data/request-budget.sqlite"

The Windows application resolves the relative database under persistent app-data. The CLI resolves it relative to the selected config file. Both require an absolute path after resolution. Never use a new DB location as a shortcut to reset the request history.

## Known limitations

1. A local verified=true flag is an assertion, not evidence of institutional permission. Independently verify official policy before any live requests.
2. Local SQLite DB and lock coordinate cooperating processes on the SAME machine. Multiple devices, a shared public IP, an uncooperative external client or separate databases need a centrally managed shared budget.
3. Cross-process implementation intentionally supports exactly one in-flight HTTP request. Multi-slot concurrency needs an audited global broker.
4. Wall-clock rollback fails closed. Forward jumps, DB tampering and clock manipulation are not independently detectable by the client.
5. A reserved request counts even if cancelled before transmission: conservative but potentially slower.
6. No live school-specific write adapter exists. Existing AttemptJournal prevents duplicate non-idempotent writes after uncertainty. Missing enrollment alone is never proof of a failed write.
7. External notification providers have their own origins and request policies, separate from the university-origin budget.
8. These changes improve coordination and reliability, not the authorized QPS or guaranteed enrollment success.

## Offline tests

Run from clients/windows/source: python -m unittest discover -s tests -q

Mock-only tests cover multi-window reservations, per-attempt accounting, 429 cooldown, cross-instance request serialization, school Date fallback, cancellation and catalog coalescing. Windows CI builds and verifies EXE only after tests pass.
