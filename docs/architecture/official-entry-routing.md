# Official NCWU entry inventory & read-only routing (phase 1)

Status: **implemented as a manual CLI diagnostic and an offline-tested planner;
NOT connected to live school selection or any enrollment POST.**

## Provenance and scope

NCWU's [2026-09-17 official course-selection notice](https://www2.ncwu.edu.cn/jiaowu/info/1008/2973.htm)
describes these student-facing entry modes:

1. The academic-system direct website with the academic-system account.
2. Unified campus authentication via the school's teaching-services portal.
3. The official 喜鹊儿 mobile app with the academic-system account.

The published notice **does not establish** independent servers, lower latency,
a second read-only course catalog API, independent quotas, or an alternate
student enrollment gateway. The direct website hostname is also used in NCWU's
2026-07 textbook notice. No unlisted IPs, origin nodes or subdomain guesses
are supported here.

## Commands (Windows source CLI)

From `clients/windows/source`:

```powershell
python main.py --official-entries
python main.py --probe-official-entries
```

The first command only prints the allowlisted inventory and performs **zero**
school requests; no `config.yaml` or account is required.

The packaged Windows GUI provides the equivalent actions under the **离线工具**
menu: **华水官方入口清单（离线）** and
**检测官方登录页可达性（手动）**. The network action prompts for consent,
runs separately from the UI thread, and is disabled during monitoring.

The second command is **explicitly initiated by the user** and performs at
most one unauthenticated HTTPS GET per known web login surface (two total).
It never follows redirects, sends cookies/passwords, retries, downloads
response bodies, scans additional ports/IPs or contacts a mobile app API.
On HTTP 403 or 429 it stops subsequent web probes. It does not run
continuously, in the background, or during normal monitoring.

Displayed duration is **login-page response-header latency only**. A status
of HTTP 200/302 only indicates the surface is reachable, **not** authenticated
access, catalog availability, available seats, valid selection time, or
successful enrollment. It must never be fed into a course selection backend
chooser.

## Separation of evidence

`LoginProbe` measures unauthenticated public login-page reachability.
`CatalogObservation` is a distinct type for future genuine, authorized
read-only adapters. Its `VERIFIED_OK` state requires both the authenticated
identity and course catalog structure to be independently verified.

`choose_readonly_catalog_route` is a **pure decision function** for future
read-only integrations; it does not create or switch an HTTP client. A route
is eligible only when:

- It is pre-registered as an official academic **web** catalog candidate.
- The observation has the same verified account scope.
- The observation is fresh and includes both identity and catalog evidence.
- The route is not rate-limited; current authentication/rate-limit failures
  fail closed rather than cycling through alternate entry points.
- A healthy current route is retained to avoid switch oscillation.

Only **one** direct catalog candidate exists in the shipped inventory. The
SSO browser login and 喜鹊儿 are *not* catalog routes. Automatic real-school
failover is therefore intentionally **not implemented**. Injected secondary
routes in tests are fictional fixtures, not discoveries about NCWU.

## Deferred acceptance gates

Before any live read-only selector integration or alternate routing:

1. Verify the *actual* selection catalog, remaining-seat semantics,
   authentic user identity, cookie scope and published access policy using
   the user's authorized session.
2. Require documented school permission for every additional service
   origin and each route's protocol contract. Never extrapolate from
   a guessed backend IP or an App's traffic.
3. Apply a shared global request budget to all probes and catalog queries.
   Auth/rate-limit/invalid-response must stop. Do not probe during congestion.
4. Preserve the durable enrollment attempt journal. Never fail over and
   repeat a write if its outcome is uncertain: reconcile against the
   official enrolled-course list first.
5. Compare response-time percentiles and errors only across the same
   business operation and comparable conditions. A login page is not a
   substitute for the catalog/selection operation.

No change to the existing `KingoClient` read-only safety gates, 60-second
local default polling policy, or offline-only enrollment FSM has been made.
