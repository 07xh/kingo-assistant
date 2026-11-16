# Latency instrumentation and the pickup schedule

Status: implemented (offline). Owner modules: `core/latency.py`,
`core/selection_optim.py`.

## Why measure at all

`tools/bench` already answers "where does a seat race get decided", and the
answer is not where most people optimise:

| Scenario | Confirmed / sent | Requests | Discover p50 | Submit p50 | Pipeline p95 |
|---|---|---|---|---|---|
| baseline (no prewarm, 60 s poll) | 1.00 | 1 | **28 118 ms** | 20.2 ms | 70 ms |
| high_freq_poll (500 ms poll) | 1.00 | 1 | 280.9 ms | 20.2 ms | 70 ms |
| prebuild | 1.00 | 1 | 280.9 ms | **9.2 ms** | **59 ms** |
| concurrent (8) | 0.125 | 8 | 280.9 ms | 87.1 ms | 1178 ms |
| multi_session / proxy_pool | 0.15 | 20 | 284.2 ms | 1042.7 ms | 1145 ms |
| pulse_burst | 0.013 | 80 | 280.9 ms | 1369.0 ms | 1481 ms |
| captcha_bypass / tamper_params / queue_skip / endpoint_exploit | 0.00 | 1 | — | — | — |

**Column semantics, stated explicitly because they are easy to misread:**

* `Confirmed / sent` is `len(success) / len(attempts)`
  (`academic_latency_sim.py:513`) — the share of *requests* that got an official
  readback, **not** the chance that a student ends up in the course. 8 concurrent
  means 1 of 8 requests succeeded, not "12.5% chance of getting the course".
* `Pipeline p95` is `client + network + queue + tx` only
  (`Attempt.total_ms`, line 140). It excludes discovery wait and the readback
  loop, so it is **not** end-to-end latency.
* The 60 000 ms and 500 ms poll intervals are **simulation inputs**, not
  deployable settings. The shipped client enforces a 30 s floor
  (`monitor_policy.ABSOLUTE_MIN_INTERVAL`) and has no verified school frequency
  permission, so the realistic delta is 30 s vs 60 s.

Reading: within those simulated inputs, discovery latency spans two orders of
magnitude while the submission pipeline spans 2×; every concurrency strategy
lowers the per-request confirmation share and raises pipeline p95. The lesson is
narrower than "go fast": a client that cannot report its own per-phase discovery
latency cannot be tuned at all, because every "optimisation" becomes guesswork.
That is what this module fixes.

## What is recorded

One JSONL row per completed read, appended to
`%LOCALAPPDATA%\KingoAssistantReadOnly\latency.jsonl`, rotated at 512 KiB:

| Field | Meaning |
|---|---|
| `kind` | `identity` / `entry` / `catalog` |
| `status` | HTTP status |
| `budget_wait_ms` | time blocked on the local quota lease — **not** network latency |
| `request_ms` | full HTTP round trip |
| `decode_ms` | bytes → text, including charset validation |
| `parse_ms` | table parsing |
| `total_ms` | end-to-end for this read |
| `bytes_read`, `rows` | payload size and parsed row count |
| `note` | e.g. `attempt=2` when a retry happened |

Never recorded: course IDs, class IDs, course names, cookies, request bodies,
response bodies, student identifiers. `tests/test_latency.py` asserts this.

`budget_wait_ms` is separated deliberately: a long quota wait looks identical to
a slow network if you only measure wall time, and the two demand opposite fixes.

### What is *not* split

`httpx` does not expose DNS / TCP / TLS sub-timings after the fact. Rather than
fake them, `latency.handshake_profile(host)` measures them once with a throwaway
socket during prewarm and reports them as advisory diagnostics only. Per-request
records carry the honest split (wait vs request vs decode vs parse).

### Reading it

```python
from core.latency import summarize_file
summarize_file(r"latency.jsonl")        # p50/p90/p95/max per phase
```

The GUI appends the same summary to an exported log, so a failed window can be
diagnosed after the fact without reproducing it:

```
时延采样（毫秒，本机统计，不含课程或账号信息）：
  样本数：128
  budget_wait_ms: p50=0.4 p95=3.1 max=12.8
  request_ms: p50=142.7 p95=310.2 max=1180.4
  decode_ms: p50=2.1 p95=4.0 max=9.7
  parse_ms: p50=11.4 p95=18.9 max=44.2
  total_ms: p50=158.9 p95=331.5 max=1203.1
```

`summary()["bottleneck"]` excludes `total_ms` on purpose — total is the sum of
the others and would always win a `max()`, so it can never identify a culprit.

## The pickup schedule

`PollingSchedule` tightens polling inside known withdrawal peaks. It is opt-in
and disabled by default, because NCWU peak times are not verified:

```yaml
strategy:
  pickup:
    enabled: true
    peak_interval_seconds: 30
    offpeak_interval_seconds: 60
    peak_windows:
      - {weekday: 0, start: "08:00", end: "09:30", label: "选课首日"}
      - {weekday: null, start: "22:00", end: "23:59", label: "截止前"}
```

Guarantees enforced in code, not in comments:

* every interval is `>= 30 s` (`ABSOLUTE_MIN_INTERVAL`), the client-wide floor;
* `peak_interval <= offpeak_interval <= 3600`;
* the effective interval is clamped into the operator-approved
  `interval_range`, so a peak window can tighten but never widen;
* windows may wrap past midnight and may be weekday-scoped;
* malformed windows raise `ValueError` at startup instead of silently not
  matching.

When `pickup.enabled` is false or the section is absent, the scheduler keeps its
original `random.uniform(low, high)` jitter — deliberately, because jitter avoids
a thundering herd of identical clients hitting the same second.

Peak windows only change *how often we look*. They never authorize submission,
never bypass the window check, and never alter the serial-write rule.

## Shared with the research CLI

`tools/kingo-live/optimizations.py` is now a re-export shim over
`core/selection_optim.py`. Before this change the same five classes existed
twice and could drift independently: the shipped client and the research tool
would disagree about what "reopened" means without anyone noticing. The research
CLI's 13 tests now exercise the client implementation directly.

`ReleaseMonitor` accepts both models: it uses an object's own `fingerprint()`
when present (research model) and otherwise builds `course_id\x1fclass_id`
(client `CourseOffering`). Unknown `remain` is treated as 0, never as available.

## Scope of the change

Wired in: recorder in `KingoClient`, pickup interval in `Scheduler`, exported-log
summary in the GUI.
Available but not yet wired: `ClockSync`, `Prewarmer`, `FailoverPlanner` still
need a native transport pool and a write adapter to attach to. They are exported
and tested so attaching them later is a wiring change, not a rewrite.
