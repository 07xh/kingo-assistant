# Current-session NCWU selected-result query, 2026-11-20

## Firsthand contract cross-check (offline)

The owner's 2026-11-03 first-party HAR includes the legacy timetable
entry GET and its inline getRequestParams(pageURL) function. This code builds
the ASCII string "xn=<year>&xq=<term>&xh=<student code>", standard Base64
encodes it with j$.base64.encode, then appends it as the sole "params" query
argument to the historical selected-results URL.

Both observed selected-results GET requests decoded as exactly the three
named fields. Their student-code values matched the user's authenticated
timetable page. The decoded year, term and code also independently matched
the showYearTerm.action response's xn, xqM and userCode in the HAR.

No actual code/ID, HAR payload, original parameter, cookie, login credential
or academic record is committed to the public repository.

## New pure-API read (no browser)

The Windows action uses the application's own verified EXE login identity.
For a separately approved and enabled query contract, it:

1. Checks the exact first-party HTTPS origin, path, year-term protocol,
   identity-binding status and the independently accepted request budget.
2. Within one same-session HTTPX client, sends a budgeted empty-body POST
   to showYearTerm.action; no credentials are saved.
3. Validates a successful JSON result, year/term formats and an exact
   constant-time account match against the current verified login account.
   Any mismatch, redirect, invalid response, 403 or 429 stops immediately.
4. Generates the Base64 query dynamically from that current response.
5. Performs one separately budgeted GET for the selected-results HTML.
   It never retries either request and never uses a historical query value.
6. Displays bounded kcdm/skbjdm candidates; never marks a successful
   submission or generates a write attestation.

The EXE retains its verified login username only in volatile RAM; the
session-expiry, explicit-clear, failed-login and quit paths erase it.
No browser login fallback or browser-cookie import is involved.

## Enabling gates

The shipped config remains FALSE/unverified. In an independently authorized
acceptance environment, the required selected-results contract is:

- verified: true
- query_verified: true
- method: GET
- path: /hsjw/wsxk/xkjg.ckdgxsxdkchj_data10319.jsp
- query_mode: current_yearterm_base64
- query_fields: {params: current_yearterm_base64}

The known_year_term contract must also be independently verified with
identity_binding_verified: true and the known empty-body POST.
A server-approved request budget must allow both read requests.

The previous queryless mode stays separate and requires its own acceptance.
Do not bypass these checks using config switches without school permission.

## Boundary still missing

The old report table containing the internal skbjdm field is not itself a
fresh, attested and exact current-round enrollment confirmation. This read
does not feed the EnrollmentEngine official selected-course records.
The live write form provider and institution-sanctioned end-to-end write
contract remain missing; automatic submission continues disabled.

Offline mock tests cover two independent budget charges, identity mismatch,
expired/429/403 responses, malformed or untrusted yearterm records, query
redaction and lack of browser interaction. They do NOT certify real school
availability or a successful enrollment.
