"""Tk-thread monitor event presentation and purely formatted status text.

Only the short 'status' event updates the live course rows here. All
worker shutdown, scheduled-run cleanup, login/session expiry and window
destroy ordering remain in windows_gui.Application.poll.
"""
from __future__ import annotations

from typing import Callable, Mapping, Sequence


def apply_monitor_status(
    app, value, *, change_detector: Callable,
) -> None:
    """Preserve old one-event status/bell/table semantics, in original order."""
    cycles, states, request_ms, parse_ms = value
    app.status.set(
        f"只读监测｜查询 {cycles} 次｜最近请求 {request_ms:.0f} ms"
        f"｜解析 {parse_ms:.1f} ms｜请以官网为准")
    newly_available = change_detector(app._previous_states, states)
    if newly_available:
        app.bell()
        app.log(
            f"检测到 {len(newly_available)} 个目标课程有余量，"
            "请前往官网确认；不代表选课成功。")
    app._previous_states = dict(states)
    for key, state in states.items():
        if app.table.exists(key):
            app.table.set(key, "status", str(state))


def format_official_probe_results(results, *, entries) -> str:
    """Same official-login probe details; these aren't elective seat metrics."""
    names = {entry.identifier: entry.name for entry in entries}
    lines = []
    for item in results:
        if item.elapsed_ms is None:
            elapsed = "—"
        else:
            elapsed = f"{item.elapsed_ms:.0f} ms"
        lines.append(
            f"{names.get(item.entry_id, '未知入口')}："
            f"{item.state.value} / HTTP {item.http_status or '—'} / {elapsed}"
        )
    return "\n".join(lines) + (
        "\n\n登录页响应速度不能用于证明选课后端速度或名额。"
    )


_SCHEDULED_REASONS = {
    "engine_completed": "自动执行结束｜按官方已选记录核验结果",
    "engine_halted": "选课引擎保护性停止｜请核对官方已选记录",
    "window_timeout": "等待官方选课窗口超时｜未提交选课",
    "window_unknown": "无法确认官方开放状态｜已停止",
    "cancelled_before_start": "预约尚未到点已取消",
    "cancelled_waiting_for_window": "等待窗口期间已取消",
    "invalid_poll_interval": "请求窗口本地策略无效｜已停止",
}


def scheduled_reason_text(reason: str) -> str:
    """Non-authoritative UI wording; never assert write success from a reason."""
    return _SCHEDULED_REASONS.get(
        reason, "自动选课任务已结束｜结果待确认")
