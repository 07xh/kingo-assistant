# Sub-second school-clock offset from `Date` rollover

Status: implemented (offline, arithmetic only). Owner module: `core/date_flip_sync.py`.

## The gap this closes

`window_probe` can bracket T0 to the probe gap — 18 ms spacing gives an 18 ms
bracket. That measurement is only worth having if the clock offset applied to it
is *tighter* than the bracket. It is not:

| Source | Resolution | What it measures |
|---|---|---|
| `time_sync` NTP path | ±10 ms | UTC, as served by `ntp.aliyun.com` |
| `time_sync` HTTP `Date` path | ±500 ms + RTT/2 | school host, one second |
| `date_flip_sync` | **±(gap between two samples)** | school host, the crossing instant |

NTP is accurate but measures the wrong clock. The gate that decides a seat race
runs on the school host; whether that host agrees with UTC is exactly the thing
you cannot assume. `window_probe`'s own notes already say so:

> `ClockSync` does not help: `Date` has 1 s resolution, so a Date-header-only
> sync cannot place T0 within a second either.

That was true of the implementation, not of `Date`. This module fixes the second
half.

## The trick

`Date` advances from `T` to `T+1` exactly when the server clock crosses an
integer second. Two samples that straddle the crossing bracket the instant
without assuming anything about path symmetry:

* sample `a`, `Date = T` — the school processed it before crossing, and never
  before it was sent, so the crossing happens **after `a.sent_epoch`**;
* sample `b`, `Date = T+1` — the school processed it at or after the crossing,
  and never after it was received, so the crossing happens **before
  `b.received_epoch`**.

```
T0 ∈ [ max(sent of every Date=T sample), min(received of every Date=T+1 sample) ]

offset = (T+1) - T0        half-width = (upper - lower) / 2
```

The width is the sampling gap, not the one-second resolution of `Date`. Sampling
every 18 ms brackets T0 to 18 ms; five bisection rounds land inside ~6 ms.

Using the send/receive walls of *every* sample rather than the narrower midpoint
of one pair is deliberate: an asymmetric path widens the bracket instead of
silently shifting the estimate, and one slow response cannot fake a tighter
bound.

## Properties enforced in code

* **A cached `Date` is refused.** `DateSample(age_seconds>0)` raises. A proxy's
  `Date` describes the proxy; using it injects an unbounded error.
* **Duplicate receive instants are refused.** Two observations claiming the same
  instant make their order unknowable, so the estimate declines to guess.
* **Retrograde `Date` is refused.** A later reception carrying an earlier second
  means the clock went backwards or the samples came from different hosts.
* **No crossing means no sub-second claim.** The estimate falls back to the best
  single sample and reports `crossed=False` with the half-second error intact.
* **A multi-second jump is flagged, not hidden.** `span_seconds > 1` still
  estimates, but `detail` says the sampling gap was too wide for the precision
  implied by the bracket.
* **No requests are issued here.** `estimate_offset` is arithmetic over samples
  already taken; `next_probe_after` and `bisect_plan` only return suggested
  instants. Callers own their budget, exactly as in `window_probe`.

## What is *not* wired

Consistent with the rest of the client, nothing emits these samples yet:

* `time_sync.TimeSync` still prefers NTP and keeps its `Date` path as a coarse
  fallback — changing that default would claim a precision the client has not
  accepted on a live account;
* the sampler (which endpoint, which budget entry point, how many probes) is the
  same missing piece `window-boundary-estimation.md` records for `window_probe`;
* `estimate_offset` accepts a saved sample log, so a future capture can be
  re-analysed offline without re-contacting the school.

So this is the clock half of "T0 sub-second boundary", finished and tested. The
request half is still blocked on the same capture as everything else.

## Relationship to the write gate

This module cannot arm anything. It produces a number and an honest error bar;
it does not read the window, decide that a window is open, or submit. The
`official_window_verified` attestation remains the only path to a burst cadence,
and it is unchanged.

## Test coverage

`tests/test_date_flip_sync.py` — 25 tests:

* empty / non-sample / too-many-sample input rejected;
* `received < sent`, out-of-range epochs, non-integral seconds rejected;
* cached (`Age>0`) samples rejected;
* duplicate receive instants rejected;
* retrograde `Date` rejected;
* a single-second sample set falls back with `crossed=False` and ≥500 ms error;
* bracket half-width equals the sampling gap, and shrinks with denser probing;
* the true offset lies inside the reported bracket for both signs;
* offset sign is positive when the school is ahead and negative when behind;
* a multi-second jump is estimated but flagged with `span_seconds > 1`;
* `last_closed` / `first_open` point at the actual straddling samples;
* out-of-order input is sorted by reception, not by trust;
* self-contradictory sample sets (closed sent after an open was received) raise;
* no bisection suggestion without a bracket, nor when the target is already met;
* suggestions land strictly inside the bracket;
* `bisect_plan` never exceeds `max_rounds`, and rejects negative rounds.
