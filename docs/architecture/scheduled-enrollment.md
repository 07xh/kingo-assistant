# Scheduled automatic-enrollment runner

The scheduled runner is a separate offline-tested coordinator for a future verified NCWU adapter.

- Waits until the configured local start time.
- Requires all engine capabilities and user/permission gates before waiting.
- Checks only the official selection-window state at a bounded 60–3600 second interval.
- Continues only when the official state is explicitly CLOSED; UNKNOWN or an exception stops.
- When OPEN, delegates to the enrollment engine, which checks the official window again before any write.
- Supports cancellation and a finite 1–1440 minute maximum wait.
- It does not poll course seats while the window is closed and does not create parallel requests.

No NCWU adapter is currently implemented. This runner is not connected to the Windows UI and cannot submit a real selection until official catalog, seat, window, enrollment-readback, and write contracts are independently verified.
