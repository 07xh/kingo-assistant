"""Rich CLI for a verified, personally authorized, read-only Kingosoft catalog."""
from __future__ import annotations

import argparse
import asyncio
from collections import deque
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from loguru import logger
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from core.client import KingoClient, parse_courses
from core.inspector import inspect_html
from core.scheduler import Scheduler
from core.monitor_policy import validate_limits
from core.time_sync import TimeSync


def validate(config: dict, check_only: bool = False) -> None:
    if not isinstance(config, dict):
        raise ValueError("配置根节点必须为对象")
    url = urlsplit(str(config["base_url"]))
    if url.scheme != "https" or not url.hostname or url.username or url.password or url.fragment:
        raise ValueError("base_url 必须是无凭据的 HTTPS 官方域名")
    protocol = config["protocol"]
    strategy = config["strategy"]
    if not check_only and protocol.get("verified") is not True:
        raise ValueError("尚未核对官方课程查询接口：protocol.verified 必须由本人验收")
    if not check_only and not protocol.get("table_marker"):
        raise ValueError("需核对官方只读课程列表的结构标识 protocol.table_marker，防止误读登录页")
    for name in ("identity_path", "identity_referer", "identity_selector",
                 "entry", "entry_referer", "list", "term_field", "columns"):
        if not check_only and protocol.get("verified") is True and not protocol.get(name):
            raise ValueError(f"真实只读协议配置不完整：protocol.{name}")
    if protocol.get("list_method", "GET").upper() not in ("GET", "POST"):
        raise ValueError("课程列表查询仅支持 GET / 已核实只读 POST")
    if strategy["mode"] not in ("burst", "monitor"):
        raise ValueError("mode 只能为 monitor 或 burst（单次定时检查）")
    if strategy.get("dry_run") is not True:
        raise ValueError("当前安全版只能运行 dry_run: true，不提供自动选课写入")
    if strategy["mode"] == "burst":
        TimeSync.parse(strategy["target_time"])
    validate_limits(strategy)
    targets = config["courses"]
    minimum_courses = 0 if check_only else 1
    if not isinstance(targets, list) or not minimum_courses <= len(targets) <= 30:
        raise ValueError("离线草稿可以为空；真实监测必须配置 1–30 门目标课程")
    if any(not isinstance(t, dict) for t in targets):
        raise ValueError("课程条目必须是对象")
    keys = [t["key"] for t in targets]
    if any(not isinstance(key, str) or not (1 <= len(key) <= 100) for key in keys):
        raise ValueError("志愿键必须是 1–100 字符的字符串")
    if len(set(keys)) != len(keys):
        raise ValueError("courses[].key 不允许重复")
    if not all(any(t.get(k) for k in ("course_id", "course_name", "teacher")) for t in targets):
        raise ValueError("课程至少提供一个真实的匹配条件")
    if not check_only and not (config.get("auth", {}).get("cookie") or __import__("os").getenv("KINGO_COOKIE")):
        raise ValueError("未找到本人官方登录的会话 Cookie；建议通过 KINGO_COOKIE 配置")


def replay_html(config: dict, filename: str) -> None:
    """Local-only redacted HTML review. Does not create a school HTTP client."""
    raw = _read_local_html(filename)
    items = parse_courses(raw, config["protocol"])
    table = Table(title="离线 HTML 回放｜本机样本，不代表学校实时课程", expand=True)
    for title in ("志愿键", "课程", "教学班", "状态"):
        table.add_column(title)
    for target in config["courses"]:
        matches = [item for item in items if Scheduler.matches(target, item)]
        if len(matches) > 1:
            label, cls = "多个教学班匹配；请配置 class_id", "—"
        elif not matches:
            label, cls = "脱敏页面未匹配目标", "—"
        else:
            entry = matches[0]
            cls = entry.class_id or "未知"
            label = ("余量未知" if entry.remain is None else
                     "本地样本余量 " + str(entry.remain))
        table.add_row(str(target["key"]), str(target.get("course_name") or target.get("course_id")),
                      cls, label)
    Console().print(table)
    Console().print("[yellow]只读取本机 HTML；没有访问教务系统、没有执行选课，也没有验证当前余量。[/yellow]")



def _read_local_html(filename: str) -> bytes:
    source = Path(filename)
    if source.is_symlink() or not source.is_file():
        raise ValueError("本机文件不存在或是符号链接")
    if source.stat().st_size > 4_000_000:
        raise ValueError("回放文件超过 4 MB")
    return source.read_bytes()


def inspect_local_html(filename: str) -> None:
    """Print only whitelisted structural hints. Never print row data."""
    hints = inspect_html(_read_local_html(filename))
    table = Table(title="脱敏 HTML 表格结构检查｜只读离线", expand=True)
    for header in ("表格序号", "数据行估计", "识别到的字段及列序号", "完整度"):
        table.add_column(header)
    for hint in hints:
        fields = ", ".join(f"{key}:{idx}" for key, idx in sorted(hint.field_indexes.items()))
        status = "可能是课程列表" if hint.required_fields_found and not hint.ambiguous_headers else "仅供人工核对"
        table.add_row(str(hint.table_number), str(hint.row_count), fields, status)
    Console().print(table)
    Console().print("[yellow]不会读取或输出表格内容、Cookie、Token、账号；识别列号只是未验证的线索。[/yellow]")


async def replay_sequence(config: dict, filenames: list[str]) -> None:
    """Run the real scheduler state transitions against local files only.

    Never creates KingoClient, school requests, remote notifications or state files.
    """
    if not 1 <= len(filenames) <= 20:
        raise ValueError("离线序列需包含 1 至 20 个文件")
    frames = [_read_local_html(name) for name in filenames]
    safe_config = deepcopy(config)
    safe_config["notify"] = {"enabled": False}
    safe_config["strategy"].pop("state_file", None)

    class OfflineClient:
        def __init__(self) -> None:
            self.stop = asyncio.Event()
            self.index = 0
        async def fetch_course_list(self, term: str, course_ids: list[str] | None = None):
            frame = frames[self.index]
            self.index += 1
            return parse_courses(frame, safe_config["protocol"], course_ids)

    client = OfflineClient()
    watcher = Scheduler(client, None, safe_config)
    table = Table(title="余量变化离线演练（非实时）", expand=True)
    for header in ("样本", "志愿键", "状态"):
        table.add_column(header)
    for i in range(len(frames)):
        await watcher.cycle()
        for target in safe_config["courses"]:
            table.add_row(str(i+1), str(target["key"]), watcher.states[target["key"]])
    Console().print(table)
    Console().print("[yellow]离线文件没有代表当前官方名额；未访问学校或发送通知。[/yellow]")


def dashboard(client: KingoClient, scheduler: Scheduler, clock: TimeSync, logs) -> Panel:
    table = Table(expand=True)
    for name in ("优先级", "目标", "教师", "监测状态"):
        table.add_column(name)
    for rank, target in enumerate(scheduler.config["courses"], 1):
        status = scheduler.states[target["key"]]
        color = "green" if status.startswith("发现余量") else "yellow"
        table.add_row(str(rank), target.get("course_name") or target.get("course_id") or "按教师匹配",
                      target.get("teacher") or "不限", Text(status, style=color))
    status = (f"已核实身份：{client.student} | 模式：{scheduler.strategy['mode']}（只读）"
              f" | 已查询：{scheduler.cycles} 次\n"
              f"最近 HTTP 请求：{client.last_ms:.1f} ms | 行解析：{client.parse_ms:.2f} ms\n"
              f"本机校时偏移估计：{clock.offset_ms:+.1f} ms | 不确定度：±{clock.uncertainty_ms:.1f} ms\n"
              f"{clock.source} | 余量只是某次查询快照，并非选课成功")
    return Panel(Group(Text(status), table, Text("\n".join(logs))), title="青果选课辅助｜本人授权只读版")


async def run(config: dict, messages: deque) -> None:
    client = KingoClient(config)
    clock = TimeSync()
    try:
        await client.identity()
        # Best effort NTP/HTTP Date, not proof of campus enrollment opening time.
        try:
            await clock.sync(client.http, client.url(config["protocol"]["identity_path"]),
                             client.url(config["protocol"]["identity_referer"]))
        except Exception as error:
            logger.warning("网络校时不可用：{}；回退本机时间", type(error).__name__)
        client.prepare_query(config["xnxq"])  # Pre-compile static GET/POST body before monitoring.
        scheduler = Scheduler(client, clock, config)
        task = asyncio.create_task(scheduler.run())
        try:
            with Live(dashboard(client, scheduler, clock, messages), refresh_per_second=2) as live:
                while not task.done():
                    live.update(dashboard(client, scheduler, clock, messages))
                    await asyncio.sleep(0.5)
                await task
                live.update(dashboard(client, scheduler, clock, messages))
        finally:
            client.stop.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    finally:
        await client.aclose()


def main() -> int:
    parser = argparse.ArgumentParser(description="青果选课：只读课程余量查询与提醒")
    parser.add_argument("-c", "--config", default="config.yaml")
    parser.add_argument("--check-config", action="store_true", help="无需网络，校验配置的基本结构")
    offline = parser.add_mutually_exclusive_group()
    offline.add_argument("--replay-html", metavar="FILE", help="离线回放脱敏 HTML")
    offline.add_argument("--inspect-html", metavar="FILE", help="只输出脱敏 HTML 表格结构提示")
    offline.add_argument("--replay-sequence", nargs="+", metavar="FILE",
                         help="多个本机 HTML 快照的余量状态演练，不发通知")
    args = parser.parse_args()
    messages = deque(maxlen=5)
    logger.remove()
    Path("logs").mkdir(exist_ok=True)
    logger.add("logs/kingo-readonly.log", rotation="5 MB", retention="7 days",
               encoding="utf-8", backtrace=False, diagnose=False,
               format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}")
    logger.add(lambda msg: messages.append(str(msg).strip()), format="{time:HH:mm:ss} | {message}")
    try:
        with open(args.config, encoding="utf-8") as source:
            config = yaml.safe_load(source)
        offline_mode = bool(args.replay_html or args.inspect_html or args.replay_sequence)
        validate(config, check_only=args.check_config or offline_mode)
        if args.inspect_html:
            inspect_local_html(args.inspect_html)
        elif args.replay_sequence:
            asyncio.run(replay_sequence(config, args.replay_sequence))
        elif args.replay_html:
            replay_html(config, args.replay_html)
        elif args.check_config:
            Console().print("[green]配置结构检查通过。[/green] 这不代表学校接口已验证。")
        else:
            asyncio.run(run(config, messages))
    except KeyboardInterrupt:
        Console().print("监测已停止。")
    except Exception as error:
        # No URL, server response or credential values in logs.
        logger.error("运行终止：{}", type(error).__name__)
        Console().print(f"[red]启动失败：{type(error).__name__}。[/red] 请检查配置和本人授权状态。")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
