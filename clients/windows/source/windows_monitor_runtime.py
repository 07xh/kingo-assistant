"""The read-only monitor engine and typed event queue; independent of Tk.

No UI controls or log presentation are accessed from the background thread.
The school protocol, query budget and cancellation semantics are preserved.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import queue
import threading

from core.client import KingoClient
from core.latency import DEFAULT_LATENCY_FILE, LatencyRecorder
from core.scheduler import Scheduler
from core.time_sync import TimeSync
from windows_config_runtime import app_dir


@dataclass
class MonitorEvent:
    kind: str
    value: object = None



class MonitorWorker:
    """Async network loop runs on a background thread. Tk lives on the main thread."""

    def __init__(self, config: dict, updates: queue.Queue[MonitorEvent],
                 *, session_cookie: str | None = None):
        self.config = config
        self.updates = updates
        self.session_cookie = session_cookie
        self.thread: threading.Thread | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.client: KingoClient | None = None
        self.latency: LatencyRecorder | None = None
        self.task: asyncio.Task | None = None
        self._stop_requested = threading.Event()
        self._had_scheduler_error = False

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            raise RuntimeError("已有一个监测任务在运行")
        self.thread = threading.Thread(target=self._thread_main, daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self._stop_requested.set()
        loop = self.loop
        client = self.client
        if loop and loop.is_running():
            def stop_in_loop() -> None:
                if client:
                    client.stop.set()
                if self.task and not self.task.done():
                    self.task.cancel()
            loop.call_soon_threadsafe(stop_in_loop)

    def _thread_main(self) -> None:
        outcome = "completed"
        try:
            asyncio.run(self._run())
        except Exception as exc:
            outcome = "failed"
            # Deliberately do not serialize server responses, URL, credentials or
            # arbitrary exception details to the GUI or log.
            self.updates.put(MonitorEvent("error", type(exc).__name__))
        finally:
            if outcome == "completed":
                if self._stop_requested.is_set():
                    outcome = "stopped"
                elif self._had_scheduler_error:
                    outcome = "failed"
            self.loop = None
            self.client = None
            self.task = None
            self.updates.put(MonitorEvent("finished", outcome))

    async def _run(self) -> None:
        self.loop = asyncio.get_running_loop()
        if self._stop_requested.is_set():
            return
        # Defence in depth: Windows monitoring cannot silently borrow a
        # KINGO_COOKIE/YAML cookie through KingoClient's legacy CLI fallback.
        # The GUI must supply the identity-verified in-process EXE session.
        if not isinstance(self.session_cookie, str) or not self.session_cookie:
            raise RuntimeError("Windows 监测必须使用本次 EXE 已核验会话")
        # Phase timings land in the app data folder; see core/latency.py.
        recorder = LatencyRecorder(app_dir() / DEFAULT_LATENCY_FILE)
        client = (KingoClient(self.config, session_cookie=self.session_cookie,
                              latency=recorder)
                  if self.session_cookie is not None
                  else KingoClient(self.config, latency=recorder))
        self.client = client
        self.latency = recorder
        clock = TimeSync()
        try:
            if self._stop_requested.is_set():
                return
            await client.identity()
            self.updates.put(MonitorEvent("message", "本人会话验证通过；只读模式。"))
            if self._stop_requested.is_set():
                return
            try:
                await clock.sync(client.http,
                                 client.url(self.config["protocol"]["identity_path"]),
                                 client.url(self.config["protocol"]["identity_referer"]),
                                 budgeted_get=lambda url, ref: client.read("GET", url, ref))
            except Exception:
                self.updates.put(MonitorEvent("message", "校时不可用，使用本机时间。"))
            client.prepare_query(self.config["xnxq"])
            scheduler = Scheduler(client, clock, self.config)
            last_cadence = scheduler.adaptive.report()
            self.updates.put(MonitorEvent("cadence", last_cadence))
            running = asyncio.create_task(scheduler.run())
            self.task = running
            last_snapshot = None
            try:
                while not running.done():
                    if self._stop_requested.is_set():
                        client.stop.set()
                    # A long scheduled wait should not enqueue identical Tk
                    # updates twice a second for hours. Emit only on change.
                    snapshot = (scheduler.cycles, tuple(sorted(scheduler.states.items())),
                                client.last_ms, client.parse_ms)
                    if snapshot != last_snapshot:
                        self.updates.put(MonitorEvent(
                            "status", (scheduler.cycles, dict(scheduler.states),
                                       client.last_ms, client.parse_ms)))
                        last_snapshot = snapshot
                    cadence = scheduler.adaptive.report()
                    if cadence != last_cadence:
                        self.updates.put(MonitorEvent("cadence", cadence))
                        last_cadence = cadence
                    await asyncio.sleep(0.5)
                await running
                cadence = scheduler.adaptive.report()
                if cadence != last_cadence:
                    self.updates.put(MonitorEvent("cadence", cadence))
                self.updates.put(MonitorEvent("status", (scheduler.cycles, dict(scheduler.states),
                                                                  client.last_ms, client.parse_ms)))
                if scheduler.last_error:
                    self._had_scheduler_error = True
                    self.updates.put(MonitorEvent("stop_reason", scheduler.last_error))
            except asyncio.CancelledError:
                self.updates.put(MonitorEvent("message", "已终止本次监测或定时检查。"))
            finally:
                client.stop.set()
                if not running.done():
                    running.cancel()
                await asyncio.gather(running, return_exceptions=True)
        finally:
            await client.aclose()


