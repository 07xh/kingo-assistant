# Native EXE login & volatile session — phase 2

This stage implements **one-shot, same-origin, verified-form authentication inside the Windows process**. It does not automate a browser or derive NCWU/CAS login fields from guesswork. It is an integration point, **not a verified working NCWU account login**.

## Runtime flow

1. The **EXE 内登录** button first performs a no-network validation: the verified identity protocol and separately verified login-form contract must be complete. No prompt for a password appears when these are missing.
2. The user enters their own account and password in local masked Tk prompts. Credentials are passed only to an in-memory, one-shot Python HTTP worker. They are not written to YAML, the plan database, the journal or logs.
3. The worker enforces HTTPS and same origin with the configured official site, allows one POST using the verified field mapping, requires an independently verified shared request-budget policy, and does not follow redirects, retry failed logins, solve CAPTCHA, or bypass CAS/SSO.
4. The response's same-origin session cookies are kept only in memory. A second, authenticated official identity read via `KingoClient` is mandatory; an HTTP 200 or cookie alone is not login success.
5. Only on successful identity proof does the GUI retain the volatile session. **EXE 接口提前预检（只读）**, **核验应用会话（只读）**, and **启动只读监测** may then reuse the cookie in their existing HTTP client. An explicit cookie overrides, but never silently falls back to, a stale environment cookie.
6. **清除本机会话** clears the volatile cookie locally. It does not claim to log out of the university server. Process quit also drops the volatile session. Windows sleep/reboot does not preserve it.

## Configuration contract (disabled by default)

```yaml
auth:
  cookie: ""
  login:
    verified: false
    method: POST
    path: ""
    username_field: ""
    password_field: ""
    static_fields: {}
```

Never put passwords, verification codes, CSRF secrets, session cookies, or real HAR captures in this YAML or Git history. `verified: true` means the operator has independently checked the official permitted endpoint, names, origin and expected response against a legitimate account and published access requirements. Setting the boolean alone does not prove this.

The generic form adapter cannot currently handle NCWU CAS redirects, dynamic anti-CSRF forms, QR-code SSO, MFA, or a different-host identity provider. Those require a separately audited and permitted identity adapter; no bypass or endpoint guessing is included.

## Current boundaries

- Authenticating in the EXE is a different operation from submitting an elective. **No NCWU enrollment-write adapter has been enabled** and the native scheduled submit button remains disabled.
- Without verified identity, protocol and server budget contracts, login and read-only operations fail closed.
- Every regression test uses synthetic user data, mocks or local in-memory HTTP transport; they do not send any request to school infrastructure.
- Formal NCWU catalog/remaining-seat/window/current-enrollments/write-and-readback verification and permitted end-to-end Windows acceptance remain open.
