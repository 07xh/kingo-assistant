"""CLI for the concurrent multi-account burst.

Two modes, both real:

``--dry-run``
    Runs the whole scheduler -- stagger, machine-wide in-flight cap,
    per-account pacing, window caps, retries, breaker, deadline -- against a
    synthetic session whose only cost is a simulated POST latency. Throughput
    numbers it prints are measured, not estimated, which is how you find out
    whether "8 concurrent" actually beats "1 every 30 s" on this machine.

``--live``
    Runs the same scheduler against real sessions. Each account must name its
    own session factory (``session_factory: "pkg.module:factory"``) because a
    session is account-bound: a cookie, a journal and a form provider all
    belong to one identity. An account without a factory is reported as
    ``no_session`` and is never contacted.
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import random
import sys
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import yaml

from core.multi_account_plan import (
    AccountPlan, AccountTarget, PlanError, build_account_plans,
)
from core.multi_account_run import (
    MultiAccountRunner, RunError, RunReport, run_summary,
)
from core.rush_profile import (
    PRESETS, RushError, RushProfile, baseline_requests_per_minute, from_mapping,
    speedup,
)


def _ensure_utf8_stdout() -> None:
    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if not callable(reconfigure):
        return
    try:
        reconfigure(encoding="utf-8", errors="replace")
    except (ValueError, OSError):
        pass


def import_object(path: str) -> Any:
    """Resolve ``pkg.module:attr`` (or ``pkg.module.attr``) to a Python object."""
    if not isinstance(path, str) or not path.strip():
        raise RunError("会话工厂路径不能为空")
    raw = path.strip()
    if ":" in raw:
        module_name, attr_path = raw.split(":", 1)
    else:
        module_name, _, attr_path = raw.rpartition(".")
    if not module_name or not attr_path:
        raise RunError(f"无法解析会话工厂路径：{path}")
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise RunError(f"会话工厂模块无法导入：{module_name}") from exc
    target: Any = module
    for part in attr_path.split("."):
        try:
            target = getattr(target, part)
        except AttributeError as exc:
            raise RunError(f"模块 {module_name} 没有 {attr_path}") from exc
    return target


class DryRunSession:
    """Synthetic session: costs one simulated POST, never opens a socket."""

    def __init__(self, latency: float, *, rng: random.Random | None = None) -> None:
        self.latency = latency
        self._rng = rng or random.Random(0x5EED)
        self.calls = 0
        self.closed = False

    async def submit(self, target) -> dict:
        self.calls += 1
        spread = self.latency * self._rng.uniform(0.8, 1.2)
        await asyncio.sleep(spread)
        return {"accepted": None, "dry_run": True,
                "course_id": target.course_id, "class_id": target.class_id}

    async def aclose(self) -> None:
        self.closed = True


def _profile(config: Mapping[str, Any], args: argparse.Namespace) -> RushProfile:
    raw = config.get("rush")
    profile = from_mapping(raw if isinstance(raw, Mapping) else {},
                           preset=args.preset)
    return profile.with_overrides(
        concurrency=args.concurrency,
        min_interval=args.min_interval,
        deadline_seconds=args.deadline,
    )


def _factories(config: Mapping[str, Any], plans: Sequence[AccountPlan]) -> dict[str, Any]:
    """Collect each account's declared session factory by exact key."""
    sessions: dict[str, Any] = {}
    sections = config.get("accounts")
    by_name: dict[str, Mapping[str, Any]] = {}
    if isinstance(sections, list):
        for index, section in enumerate(sections, start=1):
            if isinstance(section, Mapping):
                by_name[str(section.get("name") or f"account{index}")] = section
    elif isinstance(sections, Mapping):
        by_name[str(sections.get("name") or "account1")] = sections
    global_factory = None
    rush = config.get("rush")
    if isinstance(rush, Mapping) and rush.get("session_factory"):
        global_factory = rush.get("session_factory")
    for plan in plans:
        section = by_name.get(plan.name, {})
        path = section.get("session_factory") or global_factory
        if path:
            sessions[plan.name] = import_object(str(path))
    return sessions


def _report_json(report: RunReport, profile: RushProfile, extra: dict) -> str:
    payload = report.as_dict()
    payload["speedup_vs_serial"] = round(
        speedup(profile, accounts=len(report.accounts)), 2)
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=True, indent=2)


async def _run(args: argparse.Namespace) -> int:
    config_path = Path(args.config)
    if not config_path.is_file():
        print(f"配置文件不存在：{config_path}")
        return 2
    with config_path.open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    if not isinstance(config, Mapping):
        print("配置根节点必须是映射")
        return 2
    plans = build_account_plans(config)
    if args.accounts:
        wanted = {name.strip() for name in args.accounts.split(",") if name.strip()}
        plans = [plan for plan in plans if plan.name in wanted]
        if not plans:
            print(f"没有匹配的账号：{args.accounts}")
            return 2
    profile = _profile(config, args)

    if args.dry_run:
        if args.simulate_latency <= 0:
            print("--simulate-latency 必须为正数")
            return 2
        sessions: dict[str, DryRunSession] = {}

        def factory(plan: AccountPlan) -> DryRunSession:
            session = DryRunSession(args.simulate_latency)
            sessions[plan.name] = session
            return session
    else:
        declared = _factories(config, plans)
        missing = [plan.name for plan in plans if plan.name not in declared]
        if missing:
            print("以下账号没有声明 session_factory，未执行："
                  + ", ".join(missing))
            print('写法：accounts[].session_factory: "your_module:make_session"；'
                  "工厂会收到该账号的 AccountPlan，返回带 submit()/aclose() 的对象。")
            return 2

        def factory(plan: AccountPlan):
            return declared[plan.name](plan)

    runner = MultiAccountRunner(plans=plans, session_factory=factory,
                                profile=profile)
    report = await runner.run()
    if args.json:
        print(_report_json(report, profile,
                           {"mode": "dry-run" if args.dry_run else "live"}))
    else:
        print(profile.describe())
        print(run_summary(report))
    if args.dry_run and not args.json:
        baseline = baseline_requests_per_minute()
        achieved = (report.budget.get("total_sent", 0) / report.elapsed
                    if report.elapsed > 0 else 0.0)
        print(f"对照：串行 1 并发 / 30 s 基线约 {baseline:.1f} 次/分；"
              f"本次实测 {achieved * 60:.0f} 次/分"
              f"（{speedup(profile, accounts=len(plans)):.1f}× 计划速率）")
    return 0


async def _bench(args: argparse.Namespace) -> int:
    """Measure every preset against the same account plan, then rank them.

    The workload is synthesised from the configured accounts (same count, same
    egress shape) but with ``--bench-requests`` targets each, because measuring
    a pacer on five requests measures the pacer's startup, not its rate.
    """
    config_path = Path(args.config)
    with config_path.open(encoding="utf-8") as handle:
        config = yaml.safe_load(handle)
    plans = build_account_plans(config)
    count = args.bench_requests
    if count < 1:
        print("--bench-requests 必须为正整数")
        return 2
    bench_plans = [
        AccountPlan(
            name=plan.name,
            database=plan.database.with_name(f"{plan.name}-bench.sqlite"),
            origin=plan.origin,
            targets=tuple(AccountTarget(course_id=f"BENCH{i}",
                                        class_id=f"{plan.name}-{i}")
                          for i in range(count)),
            windows=plan.windows,
            max_concurrency=plan.max_concurrency,
            policy_source=plan.policy_source,
        )
        for plan in plans
    ]
    rows = []
    for name in ("safe", "standard", "extreme"):
        profile = from_mapping(config.get("rush") or {}, preset=name)
        sessions: list[DryRunSession] = []

        def factory(plan: AccountPlan) -> DryRunSession:
            session = DryRunSession(args.simulate_latency)
            sessions.append(session)
            return session

        runner = MultiAccountRunner(plans=bench_plans, session_factory=factory,
                                    profile=profile)
        report = await runner.run()
        sent = report.budget.get("total_sent", 0)
        rows.append({
            "preset": name,
            "concurrency": profile.concurrency,
            "interval": profile.effective_interval,
            "elapsed": round(report.elapsed, 3),
            "sent": sent,
            "rps": round(sent / report.elapsed, 3) if report.elapsed > 0 else 0.0,
            "rpm": round(sent / report.elapsed * 60, 1) if report.elapsed > 0 else 0.0,
            "speedup_vs_serial": round(speedup(profile, accounts=len(plans)), 2),
            "closed": all(session.closed for session in sessions),
        })
    if args.json:
        print(json.dumps({"accounts": len(plans), "requests": count, "rows": rows},
                         ensure_ascii=True, indent=2))
        return 0
    print(f"账号 {len(plans)} 个 × 每账号 {count} 次，模拟单次 POST "
          f"{args.simulate_latency:g} s，共 {len(plans) * count} 次")
    print(f"{'档位':<10}{'并发':>4}{'间隔':>8}{'发送':>6}{'耗时':>8}"
          f"{'次/秒':>8}{'次/分':>9}{'计划倍数':>9}")
    for row in rows:
        print(f"{row['preset']:<10}{row['concurrency']:>4}"
              f"{row['interval']:>8.2f}{row['sent']:>6}{row['elapsed']:>8.2f}"
              f"{row['rps']:>8.2f}{row['rpm']:>9.0f}{row['speedup_vs_serial']:>9.1f}x")
    print(f"串行基线：1 并发 / 30 s = {baseline_requests_per_minute():.1f} 次/分")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="--multi-run", description="多账号抢课并发执行（写阶段）")
    parser.add_argument("-c", "--config", default="config.yaml")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true",
                      help="合成会话跑完整调度，实测吞吐，不联网")
    mode.add_argument("--live", action="store_true",
                      help="使用各账号声明的 session_factory 真实提交")
    mode.add_argument("--bench", action="store_true",
                      help="三档预设同一计划跑分（合成会话，不联网）")
    parser.add_argument("--preset", choices=sorted(PRESETS), default=None,
                        help="覆盖 rush.preset")
    parser.add_argument("--concurrency", type=int, default=None)
    parser.add_argument("--min-interval", type=float, default=None,
                        dest="min_interval")
    parser.add_argument("--deadline", type=float, default=None)
    parser.add_argument("--accounts", default="",
                        help="只跑这些账号名，逗号分隔")
    parser.add_argument("--simulate-latency", type=float, default=0.35,
                        dest="simulate_latency",
                        help="合成会话的单次 POST 耗时（秒）")
    parser.add_argument("--bench-requests", type=int, default=12,
                        dest="bench_requests",
                        help="跑分时每账号的请求数（默认 12）")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    _ensure_utf8_stdout()
    args = _parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.bench:
            return asyncio.run(_bench(args))
        return asyncio.run(_run(args))
    except (RushError, RunError, PlanError) as error:
        print(f"配置错误：{error}")
        return 2
    except KeyboardInterrupt:
        print("已中止。")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
