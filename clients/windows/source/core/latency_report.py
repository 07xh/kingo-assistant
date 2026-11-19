"""Consumer for the latency JSONL written by ``core/latency.py``.

``core/latency.py`` records; this module reads back and answers the three
questions that matter after a run:

1. Where did the time go? (phase percentiles + the dominant component)
2. Was the bottleneck local quota waiting or the network? (``budget_share_pct``)
3. Did a change actually help? (``compare_runs`` on two captures)

Standard library only, so it works on a machine where the GUI dependencies are
absent. No course identifiers, cookies or bodies ever enter this path — it only
sees the fields ``LatencySample`` emits.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from core.latency import DEFAULT_LATENCY_FILE, PHASES, percentiles

#: Phases that are components of ``total_ms``; ``total_ms`` itself is excluded
#: from bottleneck selection because it is their sum and would always win.
COMPONENT_PHASES: tuple[str, ...] = tuple(p for p in PHASES if p != "total_ms")


class ReportError(RuntimeError):
    """The capture is unreadable or holds no usable samples."""


@dataclass(frozen=True, slots=True)
class LatencyRun:
    path: str
    samples: int
    first_at: str
    last_at: str
    kinds: dict[str, int]
    statuses: dict[str, int]
    phases: dict[str, dict]
    bottleneck: str
    budget_share_pct: float
    retried_samples: int

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "samples": self.samples,
            "first_at": self.first_at,
            "last_at": self.last_at,
            "kinds": self.kinds,
            "statuses": self.statuses,
            "phases": self.phases,
            "bottleneck": self.bottleneck,
            "budget_share_pct": self.budget_share_pct,
            "retried_samples": self.retried_samples,
        }


def load_records(path: Path | str) -> list[dict]:
    """Read a capture, skipping malformed lines instead of failing the report."""
    file_path = Path(path)
    if not file_path.exists():
        raise ReportError(f"时延采样文件不存在：{file_path}")
    if not file_path.is_file():
        raise ReportError(f"时延采样路径不是文件：{file_path}")
    records: list[dict] = []
    try:
        with file_path.open("r", encoding="utf-8") as stream:
            for line in stream:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict) and isinstance(row.get("kind"), str):
                    records.append(row)
    except (OSError, UnicodeError) as exc:
        raise ReportError(f"无法读取时延采样文件：{file_path}") from exc
    return records


def _numeric(row: dict, name: str) -> float:
    value = row.get(name, 0.0)
    return float(value) if isinstance(value, (int, float)) else 0.0


def build_run(path: Path | str, records: Sequence[dict] | None = None) -> LatencyRun:
    rows = list(records) if records is not None else load_records(path)
    if not rows:
        raise ReportError("没有可用采样：该文件里没有合法记录")
    kinds: dict[str, int] = {}
    statuses: dict[str, int] = {}
    for row in rows:
        kind = str(row.get("kind", "?"))
        kinds[kind] = kinds.get(kind, 0) + 1
        status = row.get("status")
        key = str(status) if isinstance(status, int) else "none"
        statuses[key] = statuses.get(key, 0) + 1

    phases = {name: percentiles([_numeric(row, name) for row in rows])
              for name in PHASES}
    bottleneck = max(COMPONENT_PHASES, key=lambda name: phases[name]["p50"])

    # Share of pipeline time burned waiting on the local quota lease. High values
    # mean the rate limit, not the network, is the thing to fix.
    totals = [_numeric(row, "total_ms") for row in rows]
    waits = [_numeric(row, "budget_wait_ms") for row in rows]
    paid = [(w, t) for w, t in zip(waits, totals) if t > 0]
    budget_share = (sum(w for w, _ in paid) / sum(t for _, t in paid) * 100.0
                    if paid else 0.0)

    stamps = sorted(str(row.get("at", "")) for row in rows)
    retried = sum(1 for row in rows if str(row.get("note", "")).startswith("attempt="))

    return LatencyRun(
        path=str(path),
        samples=len(rows),
        first_at=stamps[0],
        last_at=stamps[-1],
        kinds=dict(sorted(kinds.items())),
        statuses=dict(sorted(statuses.items())),
        phases=phases,
        bottleneck=bottleneck,
        budget_share_pct=round(budget_share, 2),
        retried_samples=retried,
    )


def format_run(run: LatencyRun) -> str:
    lines = [
        f"时延采样：{run.path}",
        f"样本 {run.samples} 条｜{run.first_at} → {run.last_at}",
        f"类型 {run.kinds}｜状态码 {run.statuses}｜含重试样本 {run.retried_samples}",
        "",
        f"{'阶段':<16}{'p50':>10}{'p90':>10}{'p95':>10}{'max':>10}",
    ]
    for name in PHASES:
        stats = run.phases[name]
        marker = " ←" if name == run.bottleneck else ""
        lines.append(f"{name:<16}{stats['p50']:>10.1f}{stats['p90']:>10.1f}"
                     f"{stats['p95']:>10.1f}{stats['max']:>10.1f}{marker}")
    lines.append("")
    lines.append(f"主耗时阶段：{run.bottleneck}")
    lines.append(f"本地配额等待占比：{run.budget_share_pct:.2f}%")
    if run.budget_share_pct >= 50:
        lines.append("判断：瓶颈在本地请求配额（限流），不是网络。优化连接与解析收益很小。")
    elif run.bottleneck == "request_ms":
        lines.append("判断：瓶颈在网络往返。优先看 RTT、连接复用与是否跨地区。")
    elif run.bottleneck in ("parse_ms", "decode_ms"):
        lines.append("判断：瓶颈在本地解析。可考虑字节级预筛与跳过无关节。")
    else:
        lines.append("判断：各阶段较为均衡，先确认采样量是否足够（建议 ≥ 50 条）。")
    if run.samples < 50:
        lines.append("注意：样本少于 50 条，p95 不稳定，仅作方向参考。")
    return "\n".join(lines)


def compare_runs(before: LatencyRun, after: LatencyRun,
                 phase: str = "total_ms") -> dict:
    """Percent change between two captures for one phase (p50 and p95)."""
    if phase not in PHASES:
        raise ReportError(f"未知阶段：{phase}")
    left, right = before.phases[phase], after.phases[phase]

    def delta(old: float, new: float) -> dict:
        change = new - old
        pct = (change / old * 100.0) if old else 0.0
        return {"before": round(old, 3), "after": round(new, 3),
                "delta": round(change, 3), "change_pct": round(pct, 2)}

    return {
        "phase": phase,
        "p50": delta(left["p50"], right["p50"]),
        "p95": delta(left["p95"], right["p95"]),
        "samples": {"before": before.samples, "after": after.samples},
    }


def format_comparison(result: dict) -> str:
    def line(label: str, block: dict) -> str:
        arrow = "改善" if block["delta"] < 0 else ("持平" if block["delta"] == 0 else "变差")
        return (f"{label:<6} {block['before']:>10.1f} → {block['after']:>10.1f} ms"
                f"（{block['change_pct']:+.1f}%，{arrow}）")

    return "\n".join([
        f"对比阶段：{result['phase']}",
        f"样本 {result['samples']['before']} → {result['samples']['after']}",
        line("p50", result["p50"]),
        line("p95", result["p95"]),
    ])


def latest_capture(directory: Path | str) -> Path:
    """Newest capture in a directory, following the ``*.jsonl`` rotation naming."""
    base = Path(directory)
    if not base.is_dir():
        raise ReportError(f"目录不存在：{base}")
    candidates = [p for p in base.glob("*.jsonl*") if p.is_file()]
    if not candidates:
        raise ReportError(f"目录内没有时延采样文件：{base}")
    return max(candidates, key=lambda p: p.stat().st_mtime)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="latency-report", description="本机时延采样报表（离线，不联网）")
    parser.add_argument("path", nargs="?",
                        help=f"采样文件或目录，默认 ./{DEFAULT_LATENCY_FILE}")
    parser.add_argument("--compare", metavar="OTHER",
                        help="与另一份采样对比（给出基线文件）")
    parser.add_argument("--phase", default="total_ms",
                        help="对比使用的阶段，默认 total_ms")
    parser.add_argument("--json", action="store_true", help="输出 JSON 而不是文本")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    target = Path(args.path) if args.path else Path(DEFAULT_LATENCY_FILE)
    try:
        if target.is_dir():
            target = latest_capture(target)
        run = build_run(target)
        if args.compare:
            baseline = build_run(Path(args.compare))
            result = compare_runs(baseline, run, phase=args.phase)
            print(json.dumps(result, ensure_ascii=False, indent=2)
                  if args.json else format_comparison(result))
            return 0
        print(json.dumps(run.as_dict(), ensure_ascii=False, indent=2)
              if args.json else format_run(run))
    except ReportError as error:
        print(f"无法生成报表：{error}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
