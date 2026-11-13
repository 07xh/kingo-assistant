#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Academic selection latency simulator - offline, synthetic, no network I/O.

Purpose
-------
Separate four latency stages that are usually conflated:

    client_ms    request construction / serialization / UI form preparation
    network_ms   RTT + jitter (and optional third-party relay hop)
    queue_ms     server-side admission queue (workers, capacity, 429/503)
    tx_ms        DB transaction: row lock, contention, commit, official readback

And measure three DIFFERENT speeds:

    discover_ms  how fast a client observes a free seat
    submit_ms    how fast a submission reaches the transaction boundary
    confirm_ms   how fast the server validates and confirms the enrollment

The first two are client-controllable. The third is not: it is bounded by
server queueing, rate limiting, transaction serialization and seat allocation.

Everything here runs against synthetic gateways. No school host is contacted,
no credentials are used, no real protocol field is replayed.

Usage
-----
    python academic_latency_sim.py --all
    python academic_latency_sim.py --scenario fast_poll --breakdown
    python academic_latency_sim.py --all --json result.json
    python academic_latency_sim.py --self-test
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Tuple

SEED = 20261113


# ---------------------------------------------------------------------------
# Calibrated synthetic parameters
# Format: name = primary value (plausible range), basis
# ---------------------------------------------------------------------------

@dataclass
class ServerConfig:
    # network_rtt_ms = 18 (8-45), basis: on-campus wired client to on-prem edge
    network_rtt_ms: float = 18.0
    # network_jitter_ms = 6 (0-20), basis: observed campus edge variance
    network_jitter_ms: float = 6.0
    # limiter_rate = 20 req/s (10-60), basis: illustrative gateway token bucket
    limiter_rate: float = 20.0
    limiter_burst: int = 5
    # queue_workers = 4 (1-16), queue_service_ms = 25 (10-80)
    queue_workers: int = 4
    queue_service_ms: float = 25.0
    queue_capacity: int = 64
    # tx_base_ms = 40 (20-120), tx_contention_ms = 15 (0-60)
    tx_base_ms: float = 40.0
    tx_contention_ms: float = 15.0
    # seats = 3 (1-20), basis: small elective class with scarce capacity
    seats: int = 3
    # allocation = fcfs | random | priority. This is P5, the dominant term.
    allocation: str = "fcfs"
    # lock_mode = pessimistic (serialized row lock) | optimistic (CAS version)
    lock_mode: str = "pessimistic"
    # Competing clients sharing the same seat ledger. 0 keeps single-client
    # behaviour; >0 makes relative ordering measurable instead of trivial.
    background_sessions: int = 0
    # background_spread_ms = 40000 (1000-120000), basis: one baseline poll cycle
    background_spread_ms: float = 40000.0
    retry_after_ms: float = 1000.0
    # Server-side gates. Flip these to model a defective backend.
    validate_params: bool = True
    enforce_queue_order: bool = True
    captcha_required: bool = True
    per_source_limit: bool = False
    # Observed seat release time (a withdrawal). Discovery is poll-gated.
    seat_release_ms: float = 0.0
    # Monitoring deadline = 60000 ms (1000-120000), basis: one baseline poll cycle
    deadline_ms: float = 60000.0

    def net_one_way(self, rng: random.Random) -> float:
        return max(1.0, (self.network_rtt_ms
                         + rng.uniform(-self.network_jitter_ms, self.network_jitter_ms)) / 2.0)


@dataclass
class ClientStrategy:
    name: str = "baseline"
    # poll_interval_ms = 60000 (500-60000), basis: client-side safety floor in config.example.yaml
    poll_interval_ms: float = 60000.0
    concurrency: int = 1
    burst: int = 0
    direct_api: bool = False
    sessions: int = 1
    proxy_pool: int = 1
    respect_retry_after: bool = True
    prebuild: bool = False
    race_repeat: int = 1
    tamper_params: bool = False
    exploit_endpoint: bool = False
    queue_skip: bool = False
    captcha_bypass: bool = False
    third_party_hop_ms: float = 0.0

    def client_prep_ms(self) -> float:
        # UI path = 12 (5-30); direct API = 2 (1-5); prebuild = 1 (0-3)
        if self.prebuild:
            return 1.0
        if self.direct_api:
            return 2.0
        return 12.0


@dataclass
class Attempt:
    session: int
    seq: int
    discover_ms: float = 0.0
    submit_ms: float = 0.0
    confirm_ms: float = 0.0
    client_ms: float = 0.0
    network_ms: float = 0.0
    queue_ms: float = 0.0
    tx_ms: float = 0.0
    status: int = 0
    success: bool = False
    reason: str = ""
    background: bool = False

    @property
    def total_ms(self) -> float:
        return self.client_ms + self.network_ms + self.queue_ms + self.tx_ms


# ---------------------------------------------------------------------------
# Server resources (virtual clock, milliseconds)
# ---------------------------------------------------------------------------

class TokenBucket:
    def __init__(self, cfg: ServerConfig):
        self.rate_per_ms = cfg.limiter_rate / 1000.0
        self.capacity = float(cfg.limiter_burst)
        self.tokens = float(cfg.limiter_burst)
        self.last = 0.0

    def acquire(self, now: float) -> Tuple[float, int]:
        self.tokens = min(self.capacity,
                          self.tokens + max(0.0, now - self.last) * self.rate_per_ms)
        self.last = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return 0.0, 200
        # Retry-After = time until one full token is available
        return max(1.0, (1.0 - self.tokens) / self.rate_per_ms), 429


class ServerQueue:
    def __init__(self, cfg: ServerConfig):
        self.free_at = [0.0] * max(1, cfg.queue_workers)
        self.service = cfg.queue_service_ms
        self.capacity = cfg.queue_capacity
        self.overloads = 0

    def admit(self, now: float) -> Tuple[float, int]:
        idx = min(range(len(self.free_at)), key=lambda i: self.free_at[i])
        start = max(now, self.free_at[idx])
        wait = start - now
        backlog = self.service * self.capacity / max(1, len(self.free_at))
        if wait > backlog:
            self.overloads += 1
            return wait, 503
        self.free_at[idx] = start + self.service
        return wait, 200


class SeatLedger:
    """Seat allocation with a durable official enrollment record.

    Two independent server-side properties are modelled here:

    allocation  fcfs      winners are the earliest arrivals at the tx boundary
                random    winners are drawn uniformly, arrival time is ignored
                priority  winners are ordered by session id, not by time
    lock_mode   pessimistic  serialized row lock, no aborts, tail latency grows
                optimistic   parallel tx with a CAS version check; overlapping
                             writers abort instead of waiting

    Commit decisions are deferred to settle() so that random/priority can be
    evaluated over the full candidate batch rather than one arrival at a time.
    """

    def __init__(self, cfg: ServerConfig, rng: random.Random):
        self.cfg = cfg
        self.rng = rng
        self.seats = cfg.seats
        self.taken = 0
        self.lock_free_at = 0.0
        self.commits: List[Tuple[float, int]] = []
        self.pending: List[dict] = []
        self.results: Dict[int, Tuple[bool, str]] = {}
        self.conflicts = 0
        self._next_idx = 0

    def _tx_cost(self, now: float) -> float:
        if self.cfg.lock_mode == "optimistic":
            # No row lock: parallel execution, short critical section.
            return self.cfg.tx_base_ms * 0.6
        start = max(now, self.lock_free_at)
        lock_wait = start - now
        contending = 1 if start > now else 0
        tx = self.cfg.tx_base_ms + self.cfg.tx_contention_ms * contending
        self.lock_free_at = start + tx
        return lock_wait + tx

    def commit(self, now: float, session: int, order: int) -> Tuple[float, int]:
        idx = self._next_idx
        self._next_idx += 1
        cost = self._tx_cost(now)
        self.pending.append({
            "idx": idx, "arrival": now, "session": session, "order": order,
            "cost": cost, "end": now + self.cfg.tx_base_ms * 0.6,
        })
        return cost, idx

    def settle(self) -> Dict[int, Tuple[bool, str]]:
        c = self.cfg
        conflicted: set = set()
        if c.lock_mode == "optimistic":
            # Every transaction reads the same row version. The first CAS wins;
            # any transaction whose execution window overlaps it aborts.
            last_end = -1.0
            for p in sorted(self.pending, key=lambda x: x["arrival"]):
                if p["arrival"] < last_end:
                    conflicted.add(p["idx"])
                    self.conflicts += 1
                else:
                    last_end = p["end"]

        if c.allocation == "fcfs":
            ordered = sorted(self.pending, key=lambda p: (p["order"], p["arrival"]))
        elif c.allocation == "random":
            ordered = list(self.pending)
            self.rng.shuffle(ordered)
        elif c.allocation == "priority":
            ordered = sorted(self.pending, key=lambda p: (p["session"], p["order"]))
        else:
            raise ValueError(f"unknown allocation: {c.allocation}")

        winners: Dict[int, int] = {}
        for p in ordered:
            if p["idx"] in conflicted or p["session"] in winners:
                continue
            if len(winners) >= self.seats:
                break
            winners[p["session"]] = p["idx"]
            self.commits.append((p["arrival"] + p["cost"], p["session"]))
        self.taken = len(winners)

        for p in self.pending:
            idx, sid = p["idx"], p["session"]
            if idx in conflicted:
                self.results[idx] = (False, "optimistic_conflict")
            elif sid in winners and winners[sid] == idx:
                self.results[idx] = (True, "confirmed")
            elif sid in winners:
                self.results[idx] = (False, "duplicate")
            else:
                self.results[idx] = (False, "no_seat")
        return self.results


# ---------------------------------------------------------------------------
# Simulator
# ---------------------------------------------------------------------------

class Simulator:
    def __init__(self, cfg: ServerConfig, strategy: ClientStrategy, seed: int = SEED):
        self.cfg = cfg
        self.st = strategy
        self.rng = random.Random(seed)
        self.queue = ServerQueue(cfg)
        # Allocation draws use an independent, per-seed stream so Monte Carlo
        # repeats vary the draw without perturbing the latency model.
        self.ledger = SeatLedger(cfg, random.Random(seed + 104729))
        self._deferred: List[Tuple[Attempt, int, int]] = []
        self.global_bucket = TokenBucket(cfg)
        self.source_buckets = [TokenBucket(cfg)
                               for _ in range(max(1, strategy.proxy_pool))]
        self.attempts: List[Attempt] = []
        self.discoveries: List[float] = []
        self.status_counts: Dict[int, int] = {}

    def _count(self, status: int) -> None:
        self.status_counts[status] = self.status_counts.get(status, 0) + 1

    def _guard(self) -> Tuple[Optional[int], str]:
        """Server-side integrity gates. Return (http_status, reason) or (None, '')."""
        c, s = self.cfg, self.st
        if s.tamper_params and c.validate_params:
            return 403, "param_integrity"
        if s.exploit_endpoint and c.validate_params:
            return 403, "unauthorized_route"
        if s.queue_skip and c.enforce_queue_order:
            return 403, "queue_order"
        if s.captcha_bypass and c.captcha_required:
            return 403, "captcha"
        return None, ""

    def _read_catalog(self, now: float) -> Tuple[float, int]:
        """One budgeted read-only catalog observation."""
        net = self.cfg.net_one_way(self.rng) * 2.0
        arrival = now + net
        wait, status = self.queue.admit(arrival)
        if status != 200:
            return net + wait, status
        return net + wait + self.cfg.queue_service_ms, 200

    def run(self) -> List[Attempt]:
        cfg, st = self.cfg, self.st
        decisions: List[Tuple[float, int, int, float, bool]] = []

        # --- discovery: poll-gated observation of a released seat -----------
        for sid in range(st.sessions):
            # Poll phase is centred in the interval; sessions get a small offset.
            window = min(st.poll_interval_ms, cfg.deadline_ms)
            t_poll = self.rng.uniform(0.45, 0.55) * window + sid * window * 0.01
            while t_poll <= cfg.deadline_ms:
                read_cost, status = self._read_catalog(t_poll)
                if status == 200:
                    discover = (t_poll - cfg.seat_release_ms) + read_cost
                    self.discoveries.append(discover)
                    decided_at = t_poll + read_cost
                    n_req = st.concurrency * st.race_repeat
                    if st.burst:
                        n_req *= st.burst
                    for seq in range(n_req):
                        # Concurrency spreads launches within one 2 ms scheduling slice.
                        spread = (seq % max(1, st.concurrency)) * 2.0
                        decisions.append((decided_at + spread, sid, seq,
                                          discover, False))
                    break
                t_poll += st.poll_interval_ms
            else:
                self.discoveries.append(cfg.deadline_ms)

        # Independent competitors hitting the same seat ledger. They use the
        # UI path and are spread across one baseline poll cycle, so a slow
        # client can actually be overtaken under fcfs.
        for k in range(cfg.background_sessions):
            t = self.rng.uniform(0.0, cfg.background_spread_ms)
            decisions.append((t, 10000 + k, 0, 0.0, True))

        decisions.sort(key=lambda d: d[0])

        # --- submission path -------------------------------------------------
        for order, (decide_at, sid, seq, discover, is_bg) in enumerate(decisions):
            a = Attempt(session=sid, seq=seq, discover_ms=discover,
                        background=is_bg)
            a.client_ms = 12.0 if is_bg else st.client_prep_ms()

            net_up = cfg.net_one_way(self.rng)
            net_down = cfg.net_one_way(self.rng)
            a.network_ms = net_up + net_down
            hop = 0.0 if is_bg else st.third_party_hop_ms
            arrival = decide_at + a.client_ms + net_up + hop

            def count(status: int) -> None:
                if not is_bg:
                    self._count(status)

            guard_status, guard_reason = self._guard()
            if guard_status is not None:
                a.status, a.reason = guard_status, guard_reason
                a.submit_ms = arrival - decide_at
                a.confirm_ms = 0.0
                count(guard_status)
                self.attempts.append(a)
                continue

            # Rate limiter (global unless the backend limits per source)
            bucket = (self.source_buckets[sid % len(self.source_buckets)]
                      if cfg.per_source_limit else self.global_bucket)
            retry_delay, limiter_status = bucket.acquire(arrival)
            retries = 0
            while limiter_status == 429 and retries < 3:
                count(429)
                retries += 1
                if not st.respect_retry_after and not is_bg:
                    # Ignoring Retry-After: hammer immediately, no cooldown.
                    arrival += 1.0
                else:
                    arrival += max(retry_delay, cfg.retry_after_ms)
                retry_delay, limiter_status = bucket.acquire(arrival)
            if limiter_status == 429:
                a.status, a.reason = 429, "rate_limited"
                a.submit_ms = arrival - decide_at
                a.confirm_ms = 0.0
                count(429)
                self.attempts.append(a)
                continue

            # Server admission queue -> transaction boundary
            queue_wait, queue_status = self.queue.admit(arrival)
            a.queue_ms = queue_wait
            if queue_status == 503:
                a.status, a.reason = 503, "queue_overload"
                a.submit_ms = arrival + queue_wait - decide_at
                a.confirm_ms = 0.0
                count(503)
                self.attempts.append(a)
                continue

            tx_entry = arrival + queue_wait
            a.submit_ms = tx_entry - decide_at

            tx_cost, idx = self.ledger.commit(tx_entry, sid, order)
            a.tx_ms = tx_cost

            # Official enrollment-list readback is the ONLY success evidence.
            readback_wait, readback_status = self.queue.admit(
                tx_entry + tx_cost + net_down)
            a.confirm_ms = tx_cost + net_down + readback_wait + cfg.queue_service_ms
            self._deferred.append((a, idx, readback_status))
            self.attempts.append(a)

        # Allocation is a BATCH decision: random/priority need the full
        # candidate set, so every verdict is applied after collection.
        self.ledger.settle()
        for a, idx, readback_status in self._deferred:
            ok, reason = self.ledger.results[idx]
            if readback_status == 503:
                a.status, a.reason, status = 503, "readback_overload", 503
            elif not ok:
                a.status, a.reason, status = 200, reason, 200
            else:
                a.status, a.reason, a.success = 200, "confirmed", True
                status = 200
            if not a.background:
                self._count(status)

        return self.attempts


# ---------------------------------------------------------------------------
# Scenario matrix - one row per row of the method table under study
# ---------------------------------------------------------------------------

def scenarios() -> Dict[str, ClientStrategy]:
    base = dict(poll_interval_ms=60000.0)
    return {
        "baseline":        ClientStrategy(name="baseline", **base),
        "high_freq_poll":  ClientStrategy(name="high_freq_poll", poll_interval_ms=500.0),
        "concurrent":      ClientStrategy(name="concurrent", concurrency=8, poll_interval_ms=500.0),
        "pulse_burst":     ClientStrategy(name="pulse_burst", burst=20, concurrency=4,
                                         poll_interval_ms=500.0),
        "direct_api":      ClientStrategy(name="direct_api", direct_api=True,
                                         poll_interval_ms=500.0),
        "multi_session":   ClientStrategy(name="multi_session", sessions=5, concurrency=4,
                                         poll_interval_ms=500.0),
        "proxy_pool":      ClientStrategy(name="proxy_pool", proxy_pool=5, sessions=5,
                                         concurrency=4, poll_interval_ms=500.0),
        "ignore_limit":    ClientStrategy(name="ignore_limit", respect_retry_after=False,
                                         concurrency=8, poll_interval_ms=500.0),
        "prebuild":        ClientStrategy(name="prebuild", prebuild=True,
                                         poll_interval_ms=500.0),
        "race_repeat":     ClientStrategy(name="race_repeat", race_repeat=4, concurrency=4,
                                         poll_interval_ms=500.0),
        "tamper_params":   ClientStrategy(name="tamper_params", tamper_params=True,
                                         poll_interval_ms=500.0),
        "endpoint_exploit": ClientStrategy(name="endpoint_exploit", exploit_endpoint=True,
                                          poll_interval_ms=500.0),
        "captcha_bypass":  ClientStrategy(name="captcha_bypass", captcha_bypass=True,
                                         poll_interval_ms=500.0),
        "queue_skip":      ClientStrategy(name="queue_skip", queue_skip=True,
                                         poll_interval_ms=500.0),
        "third_party":     ClientStrategy(name="third_party", third_party_hop_ms=120.0,
                                         poll_interval_ms=500.0),
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def pct(values: List[float], q: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = min(len(s) - 1, max(0, int(round((len(s) - 1) * q))))
    return round(s[idx], 1)


def summarize(name: str, sim: Simulator) -> dict:
    a = [x for x in sim.attempts if not x.background]
    succ = [x for x in a if x.success]
    return {
        "scenario": name,
        "allocation": sim.cfg.allocation,
        "lock_mode": sim.cfg.lock_mode,
        "background": sim.cfg.background_sessions,
        "optimistic_conflicts": sim.ledger.conflicts,
        "requests": len(a),
        "success": len(succ),
        "success_rate": round(len(succ) / len(a), 3) if a else 0.0,
        "discover_p50": pct(sim.discoveries, 0.50),
        "submit_p50": pct([x.submit_ms for x in a], 0.50),
        "submit_p95": pct([x.submit_ms for x in a], 0.95),
        "confirm_p50": pct([x.confirm_ms for x in succ], 0.50),
        "confirm_p95": pct([x.confirm_ms for x in succ], 0.95),
        "total_p95": pct([x.total_ms for x in a], 0.95),
        "client_p50": pct([x.client_ms for x in a], 0.50),
        "network_p50": pct([x.network_ms for x in a], 0.50),
        "queue_p50": pct([x.queue_ms for x in a], 0.50),
        "tx_p50": pct([x.tx_ms for x in a], 0.50),
        "http": dict(sorted(sim.status_counts.items())),
        "queue_overloads": sim.queue.overloads,
        "seats_taken": sim.ledger.taken,
    }


def print_table(rows: List[dict]) -> None:
    head = (f"{'scenario':<17} {'req':>4} {'ok':>3} {'rate':>6} "
            f"{'disc p50':>10} {'sub p50':>9} {'sub p95':>9} "
            f"{'cfm p50':>9} {'cfm p95':>9} {'total p95':>10}  http")
    print(head)
    print("-" * len(head))
    for r in rows:
        print(f"{r['scenario']:<17} {r['requests']:>4} {r['success']:>3} "
              f"{r['success_rate']:>6.2f} {r['discover_p50']:>10.1f} "
              f"{r['submit_p50']:>9.1f} {r['submit_p95']:>9.1f} "
              f"{r['confirm_p50']:>9.1f} {r['confirm_p95']:>9.1f} "
              f"{r['total_p95']:>10.1f}  {r['http']}")


def print_breakdown(row: dict) -> None:
    print("\nStage decomposition (p50, ms) - baseline unless overridden:")
    print(f"  client       {row['client_p50']:>8.1f}   request construction / serialization")
    print(f"  network      {row['network_p50']:>8.1f}   RTT + jitter + readback hop")
    print(f"  server queue {row['queue_p50']:>8.1f}   admission / workers / 429 / 503")
    print(f"  db tx        {row['tx_p50']:>8.1f}   row lock + contention + commit")
    print(f"  --------------------------------")
    print(f"  discover     {row['discover_p50']:>8.1f}   poll-gated catalog observation")
    print(f"  submit       {row['submit_p50']:>8.1f}   decide -> transaction boundary")
    print(f"  confirm      {row['confirm_p50']:>8.1f}   transaction -> official readback")


P5_STRATEGIES = ("baseline", "high_freq_poll", "direct_api", "prebuild",
                 "concurrent")


def p5_matrix(seats: int, background: int, spread_ms: float,
              repeats: int = 30) -> List[dict]:
    """How much does client speed buy under each server-side allocation rule?

    Each (rule, strategy) cell is a Monte Carlo mean over `repeats` independent
    seeds. A single run is a Bernoulli trial and produces misleading 0/1
    flips under random allocation. speed_gain = mean(high_freq_poll) -
    mean(baseline); a rule where it is ~0 makes client speed moot.
    """
    rows: List[dict] = []
    for alloc in ("fcfs", "random", "priority"):
        for lock in ("pessimistic", "optimistic"):
            cfg = ServerConfig(seats=seats, allocation=alloc, lock_mode=lock,
                               background_sessions=background,
                               background_spread_ms=spread_ms)
            row: dict = {"allocation": alloc, "lock_mode": lock,
                         "repeats": repeats}
            for name in P5_STRATEGIES:
                rates: List[float] = []
                conflicts = 0
                for rep in range(repeats):
                    sim = Simulator(cfg, scenarios()[name],
                                    seed=SEED + (rep + 1) * 7919)
                    sim.run()
                    rates.append(summarize(name, sim)["success_rate"])
                    conflicts += sim.ledger.conflicts
                row[name] = round(sum(rates) / len(rates), 3)
                row[name + "_min"] = min(rates)
                row[name + "_max"] = max(rates)
                row[name + "_conflicts"] = conflicts
            row["speed_gain"] = round(row["high_freq_poll"] - row["baseline"], 3)
            rows.append(row)
    return rows


def print_p5_matrix(rows: List[dict]) -> None:
    head = (f"{'allocation':<11} {'lock_mode':<13}"
            + "".join(f"{c:>17}" for c in P5_STRATEGIES)
            + f"{'speed_gain':>13}")
    print(head)
    print("-" * len(head))
    for r in rows:
        print(f"{r['allocation']:<11} {r['lock_mode']:<13}"
              + "".join(f"{r[c]:>17.2f}" for c in P5_STRATEGIES)
              + f"{r['speed_gain']:>+13.2f}")
    print("\nspeed_gain = success_rate(high_freq_poll) - success_rate(baseline)")
    print("fcfs: speed changes relative rank. random/priority: it does not.")


def self_test() -> int:
    cfg = ServerConfig()
    base = Simulator(cfg, scenarios()["baseline"]); base.run()
    fast = Simulator(cfg, scenarios()["high_freq_poll"]); fast.run()
    ignor = Simulator(cfg, scenarios()["ignore_limit"]); ignor.run()
    b, f, i = summarize("baseline", base), summarize("fast", fast), summarize("ignore", ignor)
    checks: List[Tuple[str, bool, str]] = []

    checks.append(("discovery is poll-gated",
                   f["discover_p50"] < b["discover_p50"] / 10.0,
                   f"baseline={b['discover_p50']} fast={f['discover_p50']}"))
    checks.append(("confirmation speed does NOT improve with discovery speed",
                   f["confirm_p50"] >= b["confirm_p50"] * 0.9,
                   f"baseline={b['confirm_p50']} fast={f['confirm_p50']}"))
    checks.append(("ignoring Retry-After produces more 429",
                   i["http"].get(429, 0) > b["http"].get(429, 0),
                   f"baseline={b['http'].get(429, 0)} ignore={i['http'].get(429, 0)}"))
    checks.append(("seat conservation",
                   all(r["seats_taken"] <= cfg.seats for r in (b, f, i)),
                   f"seats={cfg.seats}"))
    checks.append(("stage sums are finite",
                   all(r["total_p95"] > 0 for r in (b, f, i)),
                   f"total_p95={b['total_p95']}"))

    # --- allocation rule / lock strategy assertions -------------------------
    pb_p = Simulator(ServerConfig(lock_mode="pessimistic"),
                     scenarios()["pulse_burst"]); pb_p.run()
    pb_o = Simulator(ServerConfig(lock_mode="optimistic"),
                     scenarios()["pulse_burst"]); pb_o.run()
    sp, so = summarize("pulse_p", pb_p), summarize("pulse_o", pb_o)
    checks.append(("optimistic lock produces real version conflicts",
                   so["optimistic_conflicts"] > 0,
                   f"conflicts={so['optimistic_conflicts']}"))
    checks.append(("optimistic lock shortens tx but cannot raise success",
                   so["confirm_p50"] < sp["confirm_p50"]
                   and so["success_rate"] <= sp["success_rate"],
                   f"confirm {sp['confirm_p50']} -> {so['confirm_p50']}, "
                   f"rate {sp['success_rate']} -> {so['success_rate']}"))

    mat = p5_matrix(3, 12, 40000.0, repeats=8)
    gains = {(r["allocation"], r["lock_mode"]): r["speed_gain"] for r in mat}
    checks.append(("fcfs rewards client speed, random/priority do not",
                   gains[("fcfs", "pessimistic")] > 0.5
                   and abs(gains[("random", "pessimistic")]) < 0.25
                   and abs(gains[("priority", "pessimistic")]) < 0.25,
                   f"fcfs={gains[('fcfs','pessimistic')]} "
                   f"random={gains[('random','pessimistic')]} "
                   f"priority={gains[('priority','pessimistic')]}"))

    for label, ok, detail in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {label}: {detail}")
    failed = sum(1 for _, ok, _ in checks if not ok)
    print(f"\n{len(checks) - failed}/{len(checks)} checks passed")
    return 1 if failed else 0


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="Offline academic-selection latency simulator")
    p.add_argument("--all", action="store_true", help="run the full scenario matrix")
    p.add_argument("--scenario", choices=sorted(scenarios().keys()), default="baseline")
    p.add_argument("--breakdown", action="store_true", help="print stage decomposition")
    p.add_argument("--weak-server", action="store_true",
                   help="disable server-side integrity gates for the defect-control group")
    p.add_argument("--per-source-limit", action="store_true",
                   help="model a backend that rate-limits per source instead of globally")
    p.add_argument("--seats", type=int, default=3)
    p.add_argument("--allocation", choices=("fcfs", "random", "priority"),
                   default="fcfs", help="server-side seat allocation rule (P5)")
    p.add_argument("--lock", choices=("pessimistic", "optimistic"),
                   dest="lock_mode", default="pessimistic",
                   help="server-side transaction concurrency control")
    p.add_argument("--background", type=int, default=0,
                   help="competing clients sharing the same seat ledger")
    p.add_argument("--p5-matrix", action="store_true",
                   help="cross client-speed strategies against server rules")
    p.add_argument("--repeats", type=int, default=30,
                   help="Monte Carlo repeats per matrix cell (default 30)")
    p.add_argument("--json", metavar="PATH", help="write full results as JSON")
    p.add_argument("--self-test", action="store_true")
    args = p.parse_args(argv)

    if args.self_test:
        return self_test()

    if args.p5_matrix:
        bg = args.background if args.background > 0 else 12
        rows = p5_matrix(args.seats, bg, 40000.0, args.repeats)
        print(f"P5 sensitivity | seats={args.seats} | background={bg} | "
              f"spread=40000ms | repeats={args.repeats} | seed={SEED}\n")
        print_p5_matrix(rows)
        if args.json:
            with open(args.json, "w", encoding="utf-8") as fh:
                json.dump({"kind": "p5_matrix", "seats": args.seats,
                           "background": bg, "repeats": args.repeats,
                           "seed": SEED, "rows": rows},
                          fh, ensure_ascii=False, indent=2)
            print(f"\nJSON written to {args.json}")
        return 0

    cfg = ServerConfig(seats=args.seats,
                       validate_params=not args.weak_server,
                       enforce_queue_order=not args.weak_server,
                       captcha_required=not args.weak_server,
                       per_source_limit=args.per_source_limit,
                       allocation=args.allocation,
                       lock_mode=args.lock_mode,
                       background_sessions=args.background)

    names = sorted(scenarios().keys()) if args.all else [args.scenario]
    rows = []
    for name in names:
        sim = Simulator(cfg, scenarios()[name])
        sim.run()
        rows.append(summarize(name, sim))

    print(f"Offline simulation | seed={SEED} | seats={cfg.seats} | "
          f"limiter={cfg.limiter_rate}/s | workers={cfg.queue_workers} | "
          f"allocation={cfg.allocation} | lock={cfg.lock_mode} | "
          f"background={cfg.background_sessions} | "
          f"per_source_limit={cfg.per_source_limit} | "
          f"weak_server={args.weak_server}\n")
    print_table(rows)
    if args.breakdown or not args.all:
        print_breakdown(rows[0])

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump({"config": asdict(cfg), "rows": rows}, fh, ensure_ascii=False, indent=2)
        print(f"\nJSON written to {args.json}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
