# Architecture overview

## Product direction

KingoAssistant is intended to provide two independent clients:

- **Windows:** Python desktop application, packaged as a standalone EXE.
- **Android:** Kotlin-native application, planned as a separate client.

Neither client should require the other to operate. A cloud service is optional and is not a prerequisite for the current Windows client.

## Shared contracts

The clients may share documented data contracts and validation rules (course identity, term, seat-count observation, source, timestamp, and status). They should not be forced to share a runtime or platform-specific UI code.

## Current implementation

The Windows V4.6 baseline contains an authenticated read-only client, bounded polling scheduler, local configuration editor, offline fixtures, GUI, and packaging verifier. The configured NCWU endpoint values are not considered verified until compared with a current, authorized official read-only session.

The Android client has not been implemented.

## Safety invariants

- Read-only by default; no enrollment POST implementation in the current baseline.
- No CAPTCHA or access-control bypass.
- Stop on 401/403/429 and do not evade server limits.
- Never log credentials or personal academic data.
- Treat seat counts as observations, not successful enrollment.
