# Authorized fast polling — implementation / acceptance

The Windows app now exposes **1.5s reference**, **1s acceleration**, and **0.5s fast** presets in addition to the unchanged 60/120/300-second default options. These rates are **theoretical client settings**, not known NCWU policy or demonstrated throughput.

## Permission gate

All sub-60-second intervals fail validation unless a developer/operator has independently confirmed a **school-issued written authorization** for the exact base URL host and read-only catalog scope. Keep `strategy.rate_permission` absent while it is unknown. `confirmed: true` is a local record, **not proof** that permission exists. Do not use a hypothetical WAF limit, a benchmark of another app, inferred rate limits, or self-approval as evidence.

After independently verifying a real authorization, record the exact approved hostname, the lowest allowed gap in seconds, `scope: read-only-course-catalog`, `confirmed: true`, and a reference to the original written approval. The application checks the fields but **cannot establish authenticity**. Do not commit private approval messages or account information.

## Runtime behavior

- Exactly one scheduler cycle at a time; one course-list fetch checks all configured targets.
- Cycle-to-cycle pacing is based on start times; no catch-up bursts.
- In approved fast mode, every HTTP request made through `KingoClient.read`, including identity and initial entry navigation, is serialized with at least the configured minimum spacing.
- 403/429 stop immediately; the existing bounded 502/503 retry/backoff remains. This is not a mechanism to bypass server restrictions.
- Existing protocol verification, authentication, read-only operation and no enrollment POST are unchanged.
- Normal 60s+ configuration remains default; the request-level fast-mode gate is not applied to ordinary startup checks.

## Tests / limitations

Use only offline `httpx.MockTransport` and synthetic fixtures to test pace, rejection, and 429 behavior. GitHub Windows CI must pass before merge; do not present an unverified artifact as a release. Live NCWU elective catalog/remaining-seat data remain unverified. No automatic enrollment writes, CAPTCHA bypass, or request-limit evasion are implemented.

The 0.5-second interval corresponds to a **maximum theoretical** 120 scheduled catalog cycles/minute only if authorization exists and requests are consistently faster than the gap. Additional entry/auth checks, latency, server responses and policy gating reduce this rate.
