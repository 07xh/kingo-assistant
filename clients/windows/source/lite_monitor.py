"""Read-only monitoring lifecycle adapter for the KingoAssistant Lite sidecar.

Reuses the existing Windows MonitorWorker, scheduler, request-budget and
configuration validators. No enrollment submission gateway is constructed.
This sidecar lives only while the desktop window is open.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import queue
from typing import Any, Mapping


@dataclass
class LiteReadMonitor:
    worker: Any = field(default=None, repr=False)
    updates: queue.Queue = field(default_factory=queue.Queue, repr=False)
    phase: str = "idle"
    cycles: int = 0
    last_ms: float | None = None
    parse_ms: float | None = None
    result: str = ""
    last_message: str = ""

    def active(self) -> bool:
        thread = getattr(self.worker, "thread", None)
        return bool(thread is not None and thread.is_alive())

    def status(self) -> dict:
        for _ in range(256):
            try:
                event = self.updates.get_nowait()
            except queue.Empty:
                break
            if event.kind == "status":
                # Read only the aggregate counters; never serialize student
                # identifiers, raw school HTML, scheduler exceptions or cookies.
                try:
                    cycles, _states, request_ms, parse_ms = event.value
                    self.cycles = int(cycles)
                    self.last_ms = float(request_ms) if request_ms is not None else None
                    self.parse_ms = float(parse_ms) if parse_ms is not None else None
                except (ValueError, TypeError, OverflowError):
                    self.last_message = "监测状态不可读取"
            elif event.kind == "finished":
                self.phase = "stopped" if event.value == "stopped" else (
                    "failed" if event.value == "failed" else "completed"
                )
                self.result = self.phase
            elif event.kind == "error":
                self.last_message = "监测异常终止；请检查配置或学校访问条件"
            elif event.kind == "stop_reason":
                self.last_message = "监测由于学校接口或策略状态而停止"
            elif event.kind == "message":
                # Only known, local strings are exposed. Unexpected messages
                # are never echoed unfiltered into the React UI.
                approved = (
                    "本人会话验证通过；只读模式。",
                    "校时不可用，使用本机时间。",
                    "已终止本次监测或定时检查。",
                )
                if event.value in approved:
                    self.last_message = event.value
        if self.phase in ("running", "stopping") and not self.active():
            # Read the terminal event if it raced with the first queue pass.
            try:
                event = self.updates.get_nowait()
                if event.kind == "finished":
                    self.phase = "stopped" if event.value == "stopped" else (
                        "failed" if event.value == "failed" else "completed"
                    )
            except queue.Empty:
                pass
        return {
            "phase": self.phase,
            "active": self.active(),
            "cycles": self.cycles,
            "last_ms": self.last_ms,
            "parse_ms": self.parse_ms,
            "message": self.last_message,
            "read_only": True,
            "enrollment_available": False,
        }

    def start(self, config: Mapping, *, session_cookie: str, origin: str) -> dict:
        if self.active() or self.phase in ("running", "stopping"):
            return {"code": "busy", **self.status()}
        if origin != "jwmis.ncwu.edu.cn" or not session_cookie:
            return {"code": "missing_session", **self.status()}
        from core.desktop_settings import resolve_state_file
        from main import validate
        from windows_gui import MonitorWorker, app_dir

        # Exactly the legacy rules: verified school read-only protocol and
        # server policy, valid course targets, local data location and session.
        config = resolve_state_file(dict(config), app_dir())
        try:
            validate(config, check_only=False, session_cookie=session_cookie)
        except (ValueError, KeyError, TypeError, OSError):
            return {"code": "preflight_rejected", **self.status()}

        self.updates = queue.Queue()
        self.worker = MonitorWorker(config, self.updates, session_cookie=session_cookie)
        self.phase = "running"
        self.cycles = 0
        self.last_ms = None
        self.parse_ms = None
        self.result = ""
        self.last_message = "本次只读监测已启动，正在核验学校账号"
        try:
            self.worker.start()
        except RuntimeError:
            self.worker = None
            self.phase = "failed"
            return {"code": "start_failed", **self.status()}
        return {"code": "started", **self.status()}

    def stop(self) -> dict:
        if self.active():
            self.worker.stop()
            self.phase = "stopping"
            self.last_message = "正在停止只读监测"
            return {"code": "stop_requested", **self.status()}
        return {"code": "not_running", **self.status()}
