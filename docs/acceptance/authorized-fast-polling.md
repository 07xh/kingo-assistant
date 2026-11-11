# Fast polling: explicit opt-in and verified protocol

KingoAssistant Windows exposes 60/120/300-second ordinary presets and 1.5/1/0.5-second **experimental** read-only presets.

## Important distinction

The 1.5/1/0.5-second settings are *hypothetical client rates*, not established NCWU safe or authorized maxima. Opting in does not convert them into a confirmed school policy. Users must comply with actual university requirements.

Two mutually exclusive paths can enable sub-60-second configuration:

1. A documented `strategy.rate_permission` record matching the official host and read-only scope, with an independently verifiable source and a permitted minimum interval.
2. In the absence of a permission record, the user explicitly checks **experimental fast polling** (`strategy.fast_polling_opt_in: true`) in the editor. This is only an acknowledgement of unverified rates; it is not approval. An existing documented stricter permission always overrides the experimental opt-in.

Both paths keep the **separate school protocol validation** (`protocol.verified`) unchanged: the live catalog, remaining-seat meaning and login must be genuinely verified first. Saving local configuration is not equivalent to establishing actual access or successful enrollment.

## Controls

- Single client, serial school requests, no parallel spikes and no catch-up bursts.
- Fast mode throttles every request made through `KingoClient.read` (identity, entry, catalog, retries), not merely catalog-loop cycles.
- 403 and 429 stop without retry; persistent login/redirect challenges also stop.
- Existing HTTP error backoff, read-only interface and dry-run enforcement are retained.
- Never attempt CAPTCHA bypass, limit evasion or enrollment POSTs. Live NCWU elective catalogue and seat response semantics are still unverified.

## Acceptance

Offline unit tests and packaged Tk GUI smoke must pass in Windows CI for the new head commit. Validate the opt-in persists only when chosen and defaults to false. Synthetic fixtures use `httpx.MockTransport` and never contact an actual school. Do not present an unverified live integration as ready just because the EXE builds.
