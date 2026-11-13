#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Drive the real CLI as a subprocess against a real HTTP server.

    python cli_smoke.py

Spawns live_client.py four times (probe / monitor / enroll / measure), each
one a separate process issuing actual HTTP requests over TCP to the loopback
server started here. Prints every command, its exit code and its stdout.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

from selftest import Handler, State

HERE = Path(__file__).parent


def run(args):
    cmd = [sys.executable, str(HERE / "live_client.py")] + args
    print("\n$ " + " ".join(str(x) for x in cmd[1:]))
    p = subprocess.run(cmd, cwd=str(HERE), capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=60)
    print(f"exit={p.returncode}")
    print(p.stdout.strip()[:2000])
    if p.stderr.strip():
        print("stderr:", p.stderr.strip()[:800])
    return p


def main() -> int:
    Handler.state = State()
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    print(f"local target listening on 127.0.0.1:{port}")

    tmp = tempfile.TemporaryDirectory()
    d = Path(tmp.name)
    cfg = json.loads((HERE / "config.example.json").read_text(encoding="utf-8"))
    cfg.update({
        "base_url": f"http://127.0.0.1:{port}",
        "cookie": "",
        "verify_tls": False,
        "budget": {"rate": 200, "burst": 50, "retry_after_ms": 50,
                   "database": str(d / "budget.sqlite")},
        "journal": {"path": str(d / "intents.jsonl")},
        "targets": [{"course_id": "C001", "class_id": "T01"}],
        "strategy": {"poll_interval_ms": 1, "concurrency": 1, "burst": 0,
                     "sessions": 1, "proxies": [], "respect_retry_after": True,
                     "prebuild": False, "race_repeat": 1, "direct_api": False,
                     "timeout": 5.0, "cycles": 1},
    })
    cfg["catalog"]["path"] = "/catalog"
    cfg["submit"]["path"] = "/submit"
    cfg["enrollment"]["path"] = "/enrollment"
    cfg["clock"]["path"] = "/catalog"
    cfg["clock"]["samples"] = 3
    cfg["warmup"]["path"] = "/catalog"
    cfg_path = d / "config.local.json"
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    rec = str(d / "records.jsonl")

    base = ["--config", str(cfg_path), "--records", rec]
    checks = []
    p1 = run(base + ["--probe"])
    checks.append(("probe 200 + schema parsed", p1.returncode == 0
                   and '"status": 200' in p1.stdout
                   and "parsed=2" in p1.stdout))
    p2 = run(base + ["--monitor", "1"])
    checks.append(("monitor found a seat", p2.returncode == 0
                   and "seat=2" in p2.stdout))
    p3 = run(base + ["--enroll", "--confirm"])
    checks.append(("real submit + official readback", p3.returncode == 0
                   and '"success": true' in p3.stdout
                   and "confirmed" in p3.stdout))
    p4 = run(base + ["--measure"])
    checks.append(("four-stage latency measured", p4.returncode == 0
                   and '"queue_p50"' in p4.stdout
                   and '"tx_p50"' in p4.stdout))
    p5 = run(base + ["--sync-clock"])
    checks.append(("real Date-header clock alignment", p5.returncode == 0
                   and '"synced": true' in p5.stdout
                   and '"precision_ms": 1000.0' in p5.stdout))
    p6 = run(base + ["--warmup"])
    checks.append(("real session probe before window", p6.returncode == 0
                   and '"all_auth_ok": true' in p6.stdout))
    p7 = run(base + ["--watch-events", "1"])
    checks.append(("real catalog release event", p7.returncode == 0
                   and '"kind": "new"' in p7.stdout))
    p8 = run(base + ["--reconcile"])
    checks.append(("pending-intent reconciliation", p8.returncode == 0
                   and '"resolved"' in p8.stdout))
    p9 = run(base + ["--auto", "--confirm"])
    checks.append(("full real chain sync->warmup->watch->enroll",
                   p9.returncode == 0
                   and '"success": true' in p9.stdout
                   and '"confirmed"' in p9.stdout))
    saved = d / "probe-body.txt"
    p10 = run(base + ["--probe", "--probe-bytes", "64",
                      "--probe-save", str(saved)])
    checks.append(("probe full-body save + clock header dump",
                   p10.returncode == 0
                   and saved.exists()
                   and saved.stat().st_size > 64
                   and "Response headers relevant to clock" in p10.stdout
                   and "Full body saved to" in p10.stdout))

    server.shutdown()
    server.server_close()
    tmp.cleanup()

    print("\n" + "=" * 60)
    for label, ok in checks:
        print(f"[{'PASS' if ok else 'FAIL'}] {label}")
    bad = sum(1 for _, ok in checks if not ok)
    print(f"{len(checks) - bad}/{len(checks)} CLI checks passed")
    return 1 if bad else 0


if __name__ == "__main__":
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError, ValueError):
            pass
    sys.exit(main())
