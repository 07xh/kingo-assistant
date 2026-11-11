"""Explicitly fictional, packaged three-snapshot offline walkthrough.

No HTTP client, credentials, network request, notification, or persistence is
created by this module.  The demo is never blended with live course records.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from core.client import parse_courses
from core.desktop_settings import load_config, status_changes
from core.scheduler import Scheduler
from main import _read_local_html, validate


DEMO_FILES = (
    "snapshot-01-full.html",
    "snapshot-02-available.html",
    "snapshot-03-full-again.html",
)


@dataclass(frozen=True)
class DemoStep:
    number: int
    states: dict[str, str]
    newly_available: tuple[str, ...]


@dataclass(frozen=True)
class DemoReport:
    courses: tuple[dict, ...]
    steps: tuple[DemoStep, ...]


def run_demo(folder: Path) -> DemoReport:
    """Parse only bundled fictional HTML snapshots, using the normal matcher."""
    directory = Path(folder)
    config = load_config(directory / "offline-config.yaml")
    validate(config, check_only=True)
    if config["protocol"].get("verified") is not False:
        raise ValueError("虚构样例配置不能标记为真实核验")
    if config["strategy"].get("dry_run") is not True:
        raise ValueError("虚构样例配置必须为只读")
    old: dict[str, str] = {}
    steps = []
    for number, filename in enumerate(DEMO_FILES, 1):
        raw = _read_local_html(str(directory / filename))
        parsed = parse_courses(raw, config["protocol"])
        states = {}
        for target in config["courses"]:
            matched = [course for course in parsed if Scheduler.matches(target, course)]
            if len(matched) > 1:
                state = "虚构样例：班级匹配不唯一"
            elif not matched:
                state = "虚构样例：课程未匹配"
            elif matched[0].remain is None:
                state = "虚构样例：余量未知"
            elif matched[0].remain > 0:
                state = f"虚构样例：发现余量（{matched[0].remain}）"
            else:
                state = "虚构样例：已满"
            states[str(target["key"])] = state
        available = status_changes(old, states)
        steps.append(DemoStep(number, states, tuple(available)))
        old = states
    return DemoReport(tuple(config["courses"]), tuple(steps))
