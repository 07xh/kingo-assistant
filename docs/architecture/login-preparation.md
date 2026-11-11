# Pre-login and explicit session readiness (V5.3)

This increment is intentionally distinct from live automatic enrollment.
It does not infer permission to submit, and it does not add a school-specific
write adapter.

## Browser pre-login

- The confirmed appointment opens the fixed official NCWU portal **15 minutes
  before** the configured China-local time, once, so the user can complete the
  official login/CAPTCHA/MFA with the normal browser.
- It opens the portal again once at the confirmed time as a **manual fallback**.
  The app must stay open and Windows awake.
- A separate button opens the official portal immediately for early login.
- Browser opening is **not** proof of authentication; the app cannot read the
  browser's session, and it does not automatically submit anything.

## Script-session check

- **Check script session (read-only)** is a separate, explicit user action.
- It needs a verified official identity endpoint in the protocol configuration,
  plus a session supplied to the existing read-only HTTP client. A local
  `KINGO_COOKIE` environment variable is supported, but **do not store a
  school password, raw HAR or token in the repository or YAML**.
- The check invokes the existing same-origin HTTPS identity GET. It reports
  only whether that check succeeded, expired, was restricted, or could not be
  verified. No student name, cookie, response body or token is logged.
- A working browser login is **not** silently transferred into the script
  session. Automatic credential login is unavailable until an official,
  permitted authentication mechanism can be verified.
- No repeat login loops, CAPTCHA bypass, cross-origin redirects, or write calls.

## Remaining acceptance gates

The school-specific live elective catalog, authoritative selection window,
remaining-seat field, permitted write contract and final enrollment readback
are unverified. The existing automatic-enrollment engine is therefore still
offline-only. This work must not be described as a production auto-enroller.
