# NCWU public authentication route observations — 2026-11-16

This is a **public-source and offline-evidence inventory**, not a verified login protocol or permission to automate enrollment.

## Officially observable routing

- The university's published 2025-2026 student selection instructions describe two web entrances: the university teaching-service portal using unified authentication, and the academic department's own teaching-system entrance using an academic-system account. The same notice lists the official 喜鹊儿 app separately.
  - Source: https://www.ncwu.edu.cn/info/1063/20217.htm
- The public CAS login surface currently resolves at `authserver.ncwu.edu.cn`, with a service target on `jwmis.ncwu.edu.cn/hsjw/caslogin`. The page offers password, SMS and one-time-password authentication options. **A visible login page is not a validated password POST schema, dynamic challenge protocol or approved third-party API.**
  - Source: https://authserver.ncwu.edu.cn/authserver/login?service=https%3A%2F%2Fjwmis.ncwu.edu.cn%2Fhsjw%2Fcaslogin
- The school's 2026-10-29 academic calendar mentions 2026-11-09 through 11-13 for academic-program preselection and 2026-12-21 through 12-25 for curriculum processing and public-elective/PE arrangements. Neither is a verified student-specific precise selection-start time. **Do not auto-populate a submit timer from this calendar.**
  - Source: https://www.ncwu.edu.cn/info/1063/23332.htm

## Implementation in the Windows EXE

A new menu entry `离线工具 → 华水认证链路识别（HAR 离线）` reads a locally selected, authorized HAR file and only counts:
- CAS login-page/POST occurrences on the exact official HTTPS host
- links from CAS to the exact known teaching-system callback
- official teaching-system callback requests
- other teaching-system HTTPS requests and ignored outside traffic

The result contains **fixed status labels and aggregate counts only**. It never sends requests, exports paths, follows redirects, reads login form data, emits cookie/ticket values, or enables `protocol.verified`. Requests to look-alike hostnames do not count as official.

The `auth.login.mode` setting now distinguishes `direct_form` from `cas`. `cas` explicitly refuses credential submission before prompting, because the verified cross-origin CAS exchange and additional authentication requirements have **not** been implemented. An unrecognized mode also fails closed. The legacy form adapter remains disabled until its exact same-origin official contract is independently verified.

## What still blocks a functioning real school adapter

1. User-authorized evidence for the correct official login method, permitted integration access and any dynamic authentication requirements.
2. School-confirmed course catalog data: academic semester, course/class IDs, remaining-seat semantics and eligibility. A personal timetable JSON is not this data.
3. School-specific current enrollments, official window state, approved enrollment write operation, and authoritative readback semantics.
4. An end-to-end controlled Windows acceptance test. Offline CI is only proof of software behavior on synthetic data.

**Privacy:** Keep real HAR files only on your own machine. Never share raw credentials, session cookies, tokens, screenshots of secrets, raw HAR content or account details in repository issues, PRs, or chat. The local tool never uploads a capture.
