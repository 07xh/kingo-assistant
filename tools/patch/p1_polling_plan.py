#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P1 轮询提速与席位扩散 —— 生成 peak_windows 配置 + 全量席位差异扩散。

两件事：

1. ``plan``：按真实轮次时间（开抢 / 截止 / 补选）自动排出高峰窗口，
   直接产出可粘进 ``config.yaml`` 的 ``strategy.pickup`` 片段。
   仓库 bench 实测：发现延迟 ~28 s → 正确轮询 ~281 ms，这是最大合规收益。

2. ``widen``：目标教学班满员时，在同课程的非目标教学班里找空位，
   自动扩出备选序列（按余量降序）。配合 ``ReleaseMonitor`` 的全量 diff，
   可以捡到"别人退出来的、你原本没跟的班"。

用法::

    # 生成配置片段（默认写 stdout，可 --out 落盘）
    python p1_polling_plan.py plan --open 2026-11-23T08:00 --deadline 2026-11-27T22:00 \
        --supplemental 2026-11-28T08:00 --supplemental-end 2026-11-28T17:00 \
        --peak 30 --offpeak 60

    # 席位扩散：给一份目录导出，输出备选序列
    python p1_polling_plan.py widen --catalog catalog.json --course K0012345 \
        --exclude 3141592653589

    # 自检
    python p1_polling_plan.py selftest
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

#: 与仓库 core/selection_optim.py 的 ABSOLUTE_MIN_INTERVAL 对齐。
#: 打 v1v2 补丁后可下调（--peak 传更小值即可生效）。
ABSOLUTE_MIN_INTERVAL = 30.0


def parse_moment(text: str) -> dt.datetime:
    value = (text or "").strip()
    if not value:
        raise ValueError("时间不能为空")
    moment = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    return moment


def hhmm(moment: dt.datetime) -> str:
    return moment.strftime("%H:%M")


@dataclass(frozen=True)
class PeakWindow:
    weekday: Optional[int]
    start: str
    end: str
    label: str = ""

    def __post_init__(self) -> None:
        if self.weekday is not None and not 0 <= int(self.weekday) <= 6:
            raise ValueError("weekday 必须在 0–6")
        for name in ("start", "end"):
            text = getattr(self, name)
            if len(text) != 5 or text[2] != ":":
                raise ValueError("时间必须为 HH:MM")
            hour, minute = text[:2], text[3:]
            if not (hour.isdigit() and minute.isdigit()):
                raise ValueError("时间必须为 HH:MM")
            if not (0 <= int(hour) <= 23 and 0 <= int(minute) <= 59):
                raise ValueError("时间越界")

    def contains(self, moment: dt.datetime) -> bool:
        if self.weekday is not None and moment.weekday() != self.weekday:
            return False
        current = moment.hour * 60 + moment.minute
        start = int(self.start[:2]) * 60 + int(self.start[3:])
        end = int(self.end[:2]) * 60 + int(self.end[3:])
        if start == end:
            return True
        if start < end:
            return start <= current < end
        return current >= start or current < end

    def as_config(self) -> dict:
        return {"weekday": self.weekday, "start": self.start,
                "end": self.end, "label": self.label}


@dataclass
class Round:
    open_at: dt.datetime
    deadline: dt.datetime
    supplemental_open: Optional[dt.datetime] = None
    supplemental_end: Optional[dt.datetime] = None

    def __post_init__(self) -> None:
        if self.deadline <= self.open_at:
            raise ValueError("截止时间必须晚于开抢时间")
        if (self.supplemental_open is None) != (self.supplemental_end is None):
            raise ValueError("补选开始与结束必须同时提供")
        if self.supplemental_open and self.supplemental_end <= self.supplemental_open:
            raise ValueError("补选结束必须晚于补选开始")


def build_windows(round_: Round) -> list[PeakWindow]:
    """按轮次时间表排出高峰窗口，去重后按星期+时间排序。"""
    windows: list[PeakWindow] = []

    def add(start: dt.datetime, end: dt.datetime, label: str) -> None:
        if end <= start:
            return
        windows.append(PeakWindow(start.weekday(), hhmm(start), hhmm(end), label))

    # 1) 开抢首波：窗口刚开最密集（错选被丢弃、脚本同时涌入）
    add(round_.open_at, round_.open_at + dt.timedelta(minutes=30), "开抢首波")
    # 2) 退选潮：开抢后 2–4 小时，第一批错选被退
    add(round_.open_at + dt.timedelta(hours=2),
        round_.open_at + dt.timedelta(hours=4), "退选潮")
    # 3) 截止前两小时：最后退课高峰
    add(round_.deadline - dt.timedelta(hours=2), round_.deadline, "截止前两小时")
    # 4) 补选首日上午
    if round_.supplemental_open and round_.supplemental_end:
        add(round_.supplemental_open, round_.supplemental_end, "补选首日")

    unique: dict[tuple, PeakWindow] = {}
    for window in windows:
        key = (window.weekday, window.start, window.end)
        if key not in unique:
            unique[key] = window
    return sorted(unique.values(),
                  key=lambda w: (w.weekday if w.weekday is not None else 7,
                                 w.start, w.end))


@dataclass
class Schedule:
    peak_interval: float
    offpeak_interval: float
    windows: list[PeakWindow] = field(default_factory=list)
    floor: float = ABSOLUTE_MIN_INTERVAL

    def __post_init__(self) -> None:
        for value, label in ((self.peak_interval, "高峰间隔"),
                             (self.offpeak_interval, "平时间隔")):
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{label}无效")
            if value < self.floor or value > 3600:
                raise ValueError(
                    f"{label}必须在 {self.floor:.0f}–3600 秒之间"
                    f"（要低于 {self.floor:.0f}s 需先打 v1v2 补丁）")
        if self.peak_interval > self.offpeak_interval:
            raise ValueError("高峰间隔不应大于平时间隔")

    def active_window(self, moment: dt.datetime) -> Optional[PeakWindow]:
        for window in self.windows:
            if window.contains(moment):
                return window
        return None

    def interval(self, moment: dt.datetime) -> float:
        return (self.peak_interval if self.active_window(moment) is not None
                else self.offpeak_interval)

    def simulate(self, start: dt.datetime, hours: float,
                 step_minutes: float = 30.0) -> list[dict]:
        rows: list[dict] = []
        cursor = start
        end = start + dt.timedelta(hours=hours)
        while cursor <= end:
            window = self.active_window(cursor)
            rows.append({
                "at": cursor.strftime("%Y-%m-%d %H:%M"),
                "interval_s": self.interval(cursor),
                "window": window.label if window else "-",
            })
            cursor += dt.timedelta(minutes=step_minutes)
        return rows


def render_yaml(schedule: Schedule, *, indent: str = "  ") -> str:
    lines = [
        "strategy:",
        f"{indent}pickup:",
        f"{indent}{indent}enabled: true",
        f"{indent}{indent}peak_interval_seconds: {schedule.peak_interval:g}",
        f"{indent}{indent}offpeak_interval_seconds: {schedule.offpeak_interval:g}",
        f"{indent}{indent}peak_windows:",
    ]
    for window in schedule.windows:
        weekday = "null" if window.weekday is None else str(window.weekday)
        lines.append(
            f"{indent}{indent}{indent}- {{weekday: {weekday}, "
            f"start: \"{window.start}\", end: \"{window.end}\", "
            f"label: \"{window.label}\"}}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# 席位扩散
# ---------------------------------------------------------------------------

def offering_key(row: dict) -> str:
    return f"{row.get('course_id', '')}\x1f{row.get('class_id', '')}"


def normalize(rows: Iterable[dict]) -> list[dict]:
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        course = str(row.get("course_id") or row.get("kcdm") or "").strip()
        klass = str(row.get("class_id") or row.get("skbjdm") or "").strip()
        if not course or not klass:
            continue
        try:
            remain = int(row.get("remain") or 0)
        except (TypeError, ValueError):
            remain = 0
        out.append({
            "course_id": course,
            "class_id": klass,
            "skbh": str(row.get("skbh") or ""),
            "skbjdm": str(row.get("skbjdm") or klass),
            "name": str(row.get("name") or row.get("kcmc") or ""),
            "teacher": str(row.get("teacher") or row.get("jsxm") or ""),
            "campus": str(row.get("campus") or row.get("xq") or ""),
            "remain": remain,
        })
    return out


def widen(rows: Iterable[dict], course_id: str,
          exclude: Iterable[str] = ()) -> list[dict]:
    """同课程内按余量降序给出备选教学班，排除已锁定/已满的。"""
    blocked = {str(x) for x in exclude}
    candidates = [row for row in normalize(rows)
                  if row["course_id"] == course_id
                  and row["class_id"] not in blocked
                  and row["remain"] > 0]
    candidates.sort(key=lambda r: (-r["remain"], r["class_id"]))
    for index, row in enumerate(candidates, 1):
        row["rank"] = index
    return candidates


def diff(previous: list[dict], current: list[dict]) -> list[dict]:
    """全量目录 diff：new / reopened / increased，含非目标班级。"""
    before = {offering_key(row): row for row in normalize(previous)}
    after = {offering_key(row): row for row in normalize(current)}
    events: list[dict] = []
    for key, row in after.items():
        prior = before.get(key)
        if prior is None:
            if row["remain"] > 0:
                events.append({"kind": "new", **row})
            continue
        if prior["remain"] <= 0 < row["remain"]:
            events.append({"kind": "reopened", "previous_remain": prior["remain"], **row})
        elif row["remain"] > prior["remain"]:
            events.append({"kind": "increased", "previous_remain": prior["remain"], **row})
    return events


# ---------------------------------------------------------------------------
# 命令
# ---------------------------------------------------------------------------

def cmd_plan(args) -> int:
    try:
        round_ = Round(
            open_at=parse_moment(args.open),
            deadline=parse_moment(args.deadline),
            supplemental_open=parse_moment(args.supplemental) if args.supplemental else None,
            supplemental_end=(parse_moment(args.supplemental_end)
                              if args.supplemental_end else None),
        )
        schedule = Schedule(float(args.peak), float(args.offpeak),
                            build_windows(round_))
    except ValueError as exc:
        print(f"[x] {exc}")
        return 2

    yaml_text = render_yaml(schedule)
    print(yaml_text)
    if args.out:
        Path(args.out).write_text(yaml_text, encoding="utf-8")
        print(f"[+] 已写入 {args.out}")

    print("=== 轮询排程模拟（开抢前 2 小时起，每 30 分钟采样）===")
    for row in schedule.simulate(round_.open_at - dt.timedelta(hours=2), 30, 30):
        print(f"  {row['at']}  {row['interval_s']:>5.0f}s  {row['window']}")
    print(f"\n发现延迟上界（高峰段）= {schedule.peak_interval:g}s")
    print(f"发现延迟上界（平片段）= {schedule.offpeak_interval:g}s")
    return 0


def cmd_widen(args) -> int:
    path = Path(args.catalog)
    if not path.is_file():
        print(f"[x] catalog 不存在：{path}")
        return 2
    document = json.loads(path.read_text(encoding="utf-8"))
    rows = document
    if isinstance(document, dict):
        for key in ("offerings", "list", "items", "courses"):
            if isinstance(document.get(key), list):
                rows = document[key]
                break
    exclude = [x.strip() for x in (args.exclude or "").split(",") if x.strip()]
    candidates = widen(rows, args.course, exclude)
    if not candidates:
        print(f"[!] 课程 {args.course} 没有可用备选（全满或全部被排除）")
        return 3
    print(f"=== 课程 {args.course} 备选序列（按余量降序）===")
    for row in candidates:
        print(f"  #{row['rank']} class_id={row['class_id']} skbh={row['skbh']} "
              f"remain={row['remain']} {row['teacher']} {row['campus']}")
    if args.json:
        Path(args.json).write_text(
            json.dumps(candidates, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[+] 已写入 {args.json}")
    return 0


def _rows(document: Any) -> list[dict]:
    if isinstance(document, dict):
        for key in ("offerings", "list", "items", "courses"):
            value = document.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
        return []
    if isinstance(document, list):
        return [row for row in document if isinstance(row, dict)]
    return []


def cmd_diff(args) -> int:
    before = _rows(json.loads(Path(args.before).read_text(encoding="utf-8")))
    after = _rows(json.loads(Path(args.after).read_text(encoding="utf-8")))
    events = diff(before, after)
    if not events:
        print("无席位变化")
        return 0
    print(f"=== {len(events)} 个席位事件 ===")
    for event in events:
        print(f"  [{event['kind']:<9}] {event['course_id']}/{event['class_id']} "
              f"remain={event['remain']} ({event.get('previous_remain', '-')} -> "
              f"{event['remain']})")
    if args.json:
        Path(args.json).write_text(
            json.dumps(events, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[+] 已写入 {args.json}")
    return 0


def cmd_selftest(args) -> int:
    round_ = Round(
        open_at=dt.datetime(2026, 11, 23, 8, 0),
        deadline=dt.datetime(2026, 11, 27, 22, 0),
        supplemental_open=dt.datetime(2026, 11, 28, 8, 0),
        supplemental_end=dt.datetime(2026, 11, 28, 12, 0),
    )
    windows = build_windows(round_)
    labels = [w.label for w in windows]
    assert labels == ["开抢首波", "退选潮", "截止前两小时", "补选首日"], labels

    schedule = Schedule(30.0, 60.0, windows)
    assert schedule.interval(dt.datetime(2026, 11, 23, 8, 5)) == 30.0
    assert schedule.interval(dt.datetime(2026, 11, 23, 12, 0)) == 60.0
    assert schedule.interval(dt.datetime(2026, 11, 27, 21, 0)) == 30.0
    assert schedule.interval(dt.datetime(2026, 11, 28, 9, 0)) == 30.0
    assert schedule.interval(dt.datetime(2026, 11, 24, 9, 0)) == 60.0

    yaml_text = render_yaml(schedule)
    assert "peak_windows:" in yaml_text and yaml_text.count("- {weekday:") == 4

    rows = [
        {"course_id": "K1", "class_id": "C1", "remain": 0, "skbh": "(1)"},
        {"course_id": "K1", "class_id": "C2", "remain": 5, "skbh": "(2)"},
        {"course_id": "K1", "class_id": "C3", "remain": 12, "skbh": "(3)"},
        {"course_id": "K2", "class_id": "C4", "remain": 9, "skbh": "(9)"},
    ]
    widened = widen(rows, "K1", exclude=["C2"])
    assert [r["class_id"] for r in widened] == ["C3"], widened

    events = diff(
        [{"course_id": "K1", "class_id": "C1", "remain": 0},
         {"course_id": "K1", "class_id": "C2", "remain": 3},
         {"course_id": "K2", "class_id": "C4", "remain": 1}],
        [{"course_id": "K1", "class_id": "C1", "remain": 2},
         {"course_id": "K1", "class_id": "C2", "remain": 5},
         {"course_id": "K2", "class_id": "C4", "remain": 1},
         {"course_id": "K3", "class_id": "C5", "remain": 4}])
    kinds = sorted(e["kind"] for r in events for e in [r])
    assert kinds == ["increased", "new", "reopened"], kinds

    print("PASS selftest")
    print(f"  windows={len(windows)} peak={schedule.peak_interval:g}s "
          f"offpeak={schedule.offpeak_interval:g}s")
    print(f"  widen(K1, exclude C2) -> {[r['class_id'] for r in widened]}")
    print(f"  diff kinds -> {kinds}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="P1 轮询提速与席位扩散（生成 config.yaml 片段）")
    sub = parser.add_subparsers(dest="command", required=True)

    plan = sub.add_parser("plan", help="按轮次时间生成 peak_windows")
    plan.add_argument("--open", required=True, help="开抢时间 ISO，如 2026-11-23T08:00")
    plan.add_argument("--deadline", required=True)
    plan.add_argument("--supplemental", default=None)
    plan.add_argument("--supplemental-end", default=None)
    plan.add_argument("--peak", default=str(ABSOLUTE_MIN_INTERVAL))
    plan.add_argument("--offpeak", default="60")
    plan.add_argument("--out", default=None)
    plan.set_defaults(func=cmd_plan)

    widen_p = sub.add_parser("widen", help="同课程备选教学班扩散")
    widen_p.add_argument("--catalog", required=True)
    widen_p.add_argument("--course", required=True)
    widen_p.add_argument("--exclude", default="")
    widen_p.add_argument("--json", default=None)
    widen_p.set_defaults(func=cmd_widen)

    diff_p = sub.add_parser("diff", help="两次目录快照对比")
    diff_p.add_argument("--before", required=True)
    diff_p.add_argument("--after", required=True)
    diff_p.add_argument("--json", default=None)
    diff_p.set_defaults(func=cmd_diff)

    selftest = sub.add_parser("selftest", help="自检")
    selftest.set_defaults(func=cmd_selftest)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
