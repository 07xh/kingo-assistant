# Selection-window boundary estimation

Status: implemented (offline, arithmetic only). Owner module: `core/window_probe.py`.

## The gap this closes

The engine's window gate is binary per read — `OPEN` / `CLOSED` / `UNKNOWN` —
which is enough to refuse a write, and nothing more. But the question that
decides a seat race is not "is it open" but **"when exactly does the server
flip, relative to the announced start time"**:

* start too early → the server still answers `当前时间不是有效的选课时间区段`,
  and every one of those attempts is a wasted request against the budget;
* start too late → the seats are gone before the first write.

A 60 s poll cannot resolve a sub-second flip. So the client currently has no way
to learn the offset at all, and `ClockSync` does not help: `Date` has 1 s
resolution, so a Date-header-only sync cannot place T0 within a second either.

## What it does

Given a sequence of probes, each `(local_epoch, phase)`, it locates the first
`CLOSED -> OPEN` transition and reports:

| Field | Meaning |
|---|---|
| `last_closed_local` | last observation that still said closed |
| `observed_flip_local` | first observation that said open |
| `uncertainty_ms` | width of the bracket — the flip happened inside it |
| `server_flip_epoch` | `observed_flip_local + offset`, only when an offset is supplied |
| `recommended_lead_ms` | where to start the next burst, derived from the bracket |
| `reopened` | whether a second `CLOSED -> OPEN` transition was seen |

Key properties, all enforced in code and covered by tests:

* **Denser probing tightens the bracket.** 3 s spacing gives ±3 s; 200 ms spacing
  gives ±200 ms. That is the entire value of the module, and it is measurable.
* **UNKNOWN probes never anchor a boundary.** A failed or ambiguous read cannot
  shorten the interval.
* **Out-of-order input is sorted, duplicate timestamps are rejected.** Two
  probes claiming the same instant make the order unknowable, so the estimate
  refuses rather than guesses.
* **No offset means no guess.** Without `clock_offset_ms`, `server_flip_epoch` is
  `None`. Absolute server time is never fabricated from local time.
* **`network_half_rtt_ms` only widens.** A CLOSED response may have been decided
  before it arrived, so the band grows; it never shrinks.

## The safety tension, handled explicitly

Dense probing is exactly the behaviour a rate-limited client must not drift into.
`ProbeSchedule` therefore defaults `min_interval_ms` to the client-wide **30 s**
floor, and constructing a 200 ms schedule raises:

```
ValueError: 探测间隔 200 ms 低于已声明的许可下限 30000 ms；
            需要更密的探测必须在调用处显式下调 min_interval_ms 并具备证据
```

To burst, the caller has to write `min_interval_ms=200` **and** hold an
`official_window_verified` attestation whose evidence documents the permitted
probe cadence. The decision is visible at the call site instead of hiding in a
default — which is the failure mode this module was built to avoid.

Additional caps: `MAX_SCHEDULE_LENGTH = 200` probes per burst (checked in the
constructor, not lazily), each offset inside `[-lead, +tail]`.

## What is *not* wired

`estimate_flip` consumes probes; nothing in the client emits them yet, because a
probe is a real request and the window endpoint's semantics are still unverified
(`docs/acceptance/ncwu-original-har-contract-audit-20261116.md`). The pieces that
exist:

* `Scheduler` still polls at its configured interval and only feeds the binary
  gate;
* the probe source (which response field or status code means CLOSED) needs the
  same HAR evidence as the catalog;
* `replay()` accepts a saved `(epoch, phase)` log so a real capture can be
  re-analysed offline without re-contacting the school.

So this is the arithmetic half of "T0 sub-second boundary", finished and tested;
the measurement half is blocked on the same capture as everything else.

## Test coverage

`tests/test_window_probe.py` — 26 tests:

* bracket width equals the probe gap, and shrinks with denser probing;
* already-open-on-first-probe yields an upper bound only (`last_closed_local`
  is `None`, uncertainty 0, detail says so);
* all-closed raises `尚未翻转`;
* UNKNOWN cannot shorten the bracket;
* reopen is flagged while the *first* transition is used;
* clock offset produces the server instant, absence produces `None`;
* half-RTT widens the band by exactly that amount;
* lead is non-negative and never exceeds the band;
* 200 ms schedule rejected at the default floor; accepted when the floor is
  lowered explicitly; oversized schedule rejected at construction.
