# EXE-native API preparation — phase 1 (read-only)

Goal: a Windows EXE that authenticates and communicates through independently verified, authorized official API contracts, without operating a browser. This phase implements the read-only preparation step; it does **not** implement automatic account/password login, any NCWU write adapter, or production enrollment.

## New operation

The primary GUI now offers **EXE 接口提前预检（只读）**. One explicit click:

1. Checks the configured school catalog and identity contracts have been independently marked verified and contain the required fields.
2. Requires an application-owned authorized session (currently `KINGO_COOKIE`, never imported from the browser).
3. Requires a concrete semester and course code, not demo placeholder data.
4. Instantiates the existing `KingoClient`, using its same-origin HTTPS checks and verified persistent request budget.
5. Performs an official identity GET, then one catalog read for configured target course codes.
6. Reports counts and an explicit caveat that neither the election window nor a write adapter has been verified. No identity, Cookie, HTML, URL, or course name appears in UI results.
7. Closes its network client even on errors. Auth expired, restricted, unverified and unavailable are distinct outcomes.

The operation is performed off the Tk event loop. It does not open any browser, request a login page automatically, submit enrollment, scan endpoints, bypass a challenge or perform high-rate polling. Its application session is not silently copied from the user's browser.

## Outstanding production gates

- Verify NCWU's permitted authentication protocol; build an account-password/SSO adapter in the EXE only from an approved and tested contract.
- Verify the genuine authenticated elective catalog and remaining-seats semantics against the official UI during a permitted selection window.
- Implement a separately reviewed `EnrollmentEngine` gateway that reads official enrollment records and selection-window state, uses permitted serial writes, and confirms results from official readback.
- Test actual end-to-end integration on Windows. Synthetic HTTP fixtures and a CI packaging pass are not proof of real campus enrollment.

Manual official browser actions remain in **手动辅助** as fallback; they are neither the primary API operation nor an alternative path that could automatically enable the submission button.
