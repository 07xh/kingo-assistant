"""Standalone Windows desktop UI for the existing read-only Kingosoft assistant.

This window does NOT perform enrollment, CAPTCHA processing or concurrency bursts.
The same core.client/core.scheduler are shared with the CLI, with strictly
bounded read-only behavior and fail-closed protocol verification.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
from pathlib import Path
import queue
import shutil
import sys
import threading
import time
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk

from loguru import logger

from core.client import KingoClient, parse_courses
from core.desktop_settings import load_config, resolve_state_file, status_changes
from core.demo import DemoReport, run_demo
from desktop_editor import SettingsEditor
from desktop_plans import PlanManager
from core.inspector import inspect_html
from core.har_audit import summarize_har, format_summary
from tools.catalog_evidence import inspect_local_file
from core.scheduler import Scheduler
from core.preflight import inspect_readiness, split_checks, format_checks
from core.offline_selftest import run_selftest
from core.time_sync import TimeSync
from main import _read_local_html, validate

APP_TITLE = "青果选课辅助 · Windows V4.9（只读）"
OFFICIAL_PORTAL = "https://jwmis.ncwu.edu.cn"  # Confirmed by school notices; NOT an API endpoint.


def app_dir() -> Path:
    """Never write user data inside the PyInstaller bundle/temp folder."""
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if root:
            return Path(root) / "KingoAssistantReadOnly"
    return Path.home() / ".kingo-assistant-readonly"


def bundled_config() -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return root / "config.example.yaml"


def bundled_demo() -> Path:
    """Read-only fictional fixtures embedded in both source and one-file EXE."""
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return root / "demo"


def ensure_config() -> Path:
    dest_dir = app_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "config.yaml"
    if not dest.exists():
        source = bundled_config()
        if not source.is_file():
            raise FileNotFoundError("安装资源中缺少 config.example.yaml 模板")
        shutil.copyfile(source, dest)
    return dest


def read_config(path: Path, *, offline: bool = False) -> dict:
    config = load_config(path)
    validate(config, check_only=offline)
    return config


@dataclass
class MonitorEvent:
    kind: str
    value: object = None


class MonitorWorker:
    """Async network loop runs on a background thread. Tk lives on the main thread."""

    def __init__(self, config: dict, updates: queue.Queue[MonitorEvent]):
        self.config = config
        self.updates = updates
        self.thread: threading.Thread | None = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.client: KingoClient | None = None
        self.task: asyncio.Task | None = None
        self._stop_requested = threading.Event()

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
        try:
            asyncio.run(self._run())
        except Exception as exc:
            # Deliberately do not serialize server responses, URL, credentials or
            # arbitrary exception details to the GUI or log.
            self.updates.put(MonitorEvent("error", type(exc).__name__))
        finally:
            self.loop = None
            self.client = None
            self.task = None
            self.updates.put(MonitorEvent("finished"))

    async def _run(self) -> None:
        self.loop = asyncio.get_running_loop()
        if self._stop_requested.is_set():
            return
        client = KingoClient(self.config)
        self.client = client
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
                                 client.url(self.config["protocol"]["identity_referer"]))
            except Exception:
                self.updates.put(MonitorEvent("message", "校时不可用，使用本机时间。"))
            client.prepare_query(self.config["xnxq"])
            scheduler = Scheduler(client, clock, self.config)
            running = asyncio.create_task(scheduler.run())
            self.task = running
            try:
                while not running.done():
                    if self._stop_requested.is_set():
                        client.stop.set()
                    self.updates.put(MonitorEvent("status", (scheduler.cycles, dict(scheduler.states),
                                                                  client.last_ms, client.parse_ms)))
                    await asyncio.sleep(0.5)
                await running
                self.updates.put(MonitorEvent("status", (scheduler.cycles, dict(scheduler.states),
                                                                  client.last_ms, client.parse_ms)))
                if scheduler.last_error:
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


class Application(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("960x650")
        self.minsize(750, 520)
        self.events: queue.Queue[MonitorEvent] = queue.Queue()
        self.worker: MonitorWorker | None = None
        self._previous_states: dict[str, str] = {}
        self._demo_report: DemoReport | None = None
        self._demo_index = 0
        self._closing = False
        self.path = tk.StringVar(value=str(ensure_config()))
        self.status = tk.StringVar(value="未启动｜本机离线检查可直接使用")
        self._build()
        menu = tk.Menu(self)
        tools_menu = tk.Menu(menu, tearoff=False)
        tools_menu.add_command(label="本机 HAR 结构检查（脱敏统计）", command=self.audit_local_har)
        tools_menu.add_command(label="选课目录证据对照（离线 JSON）", command=self.review_catalog_evidence)
        menu.add_cascade(label="离线工具", menu=tools_menu)
        self.configure(menu=menu)
        self.after(300, self.poll)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.check_config()

    def _build(self) -> None:
        top = ttk.Frame(self, padding=(16, 14))
        top.pack(fill="x")
        ttk.Label(top, text="青果选课辅助 · Windows V4.9", font=("Microsoft YaHei UI", 16, "bold")).pack(anchor="w")
        ttk.Label(top, text="本机独立运行｜只读课程余量和提醒｜不代表选课成功", foreground="#666").pack(anchor="w", pady=(4, 0))

        cfg = ttk.Frame(self, padding=(16, 6))
        cfg.pack(fill="x")
        ttk.Label(cfg, text="配置文件").pack(side="left")
        self.config_entry = ttk.Entry(cfg, textvariable=self.path)
        self.config_entry.pack(side="left", fill="x", expand=True, padx=(8, 6))
        self.pick_button = ttk.Button(cfg, text="选择…", command=self.pick_config)
        self.pick_button.pack(side="left")
        self.open_button = ttk.Button(cfg, text="打开文件", command=self.open_config)
        self.open_button.pack(side="left", padx=5)
        self.edit_button = ttk.Button(cfg, text="编辑课程和设置", command=self.edit_settings)
        self.edit_button.pack(side="left", padx=5)
        self.plans_button = ttk.Button(cfg, text="课程方案", command=self.manage_plans)
        self.plans_button.pack(side="left", padx=5)

        actions = ttk.Frame(self, padding=(16, 8))
        actions.pack(fill="x")
        self.check_button = ttk.Button(actions, text="检查配置", command=self.check_config)
        self.check_button.pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="运行准备度", command=self.preflight).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="脱敏 HTML 离线演练", command=self.replay_local).pack(side="left", padx=(0, 8))
        ttk.Button(actions, text="虚构样例：下一步", command=self.demo_next).pack(side="left", padx=(0, 8))
        self.start_button = ttk.Button(actions, text="启动只读监测", command=self.start_monitor)
        self.start_button.pack(side="left", padx=(0, 8))
        self.stop_button = ttk.Button(actions, text="停止", command=self.stop_monitor, state="disabled")
        self.stop_button.pack(side="left")
        ttk.Button(actions, text="浏览器打开官方教务", command=self.open_official_portal).pack(side="right")

        ttk.Label(self, textvariable=self.status, padding=(16, 6)).pack(fill="x")
        frame = ttk.LabelFrame(self, text="目标课程（必须根据官方页面核对；以下数据不代表实时）", padding=8)
        frame.pack(fill="both", expand=True, padx=16, pady=(2, 8))
        self.table = ttk.Treeview(frame, columns=("key", "course", "teacher", "status"), show="headings")
        for col, label, width in (("key", "志愿", 90), ("course", "课程/代码", 250),
                                   ("teacher", "教师", 140), ("status", "状态", 300)):
            self.table.heading(col, text=label)
            self.table.column(col, width=width, stretch=True)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.table.pack(fill="both", expand=True)

        log_frame = ttk.LabelFrame(self, text="本地运行信息（不展示账号、Cookie 或网页原文）", padding=8)
        log_frame.pack(fill="both", expand=True, padx=16, pady=(0, 10))
        self.log_box = tk.Text(log_frame, height=7, state="disabled", wrap="word")
        self.log_box.pack(fill="both", expand=True)
        ttk.Button(log_frame, text="导出本机运行日志", command=self.export_log).pack(anchor="e", pady=(5, 0))

    def log(self, message: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", time.strftime("%H:%M:%S") + "  " + message + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def edit_settings(self) -> None:
        if self.worker:
            messagebox.showinfo("请先停止监测", "运行期间无法修改配置，请先停止当前任务。")
            return
        try:
            SettingsEditor(self, Path(self.path.get()), self.check_config)
        except (ValueError, OSError, KeyError) as exc:
            self.log("配置编辑器无法启动：" + type(exc).__name__)
            messagebox.showerror("无法打开编辑器", "配置文件结构可能损坏，请先点击检查配置。")

    def manage_plans(self) -> None:
        if self.worker:
            messagebox.showinfo("请先停止监测", "运行期间不能切换课程方案。")
            return
        try:
            PlanManager(self, config_path=Path(self.path.get()),
                        plans_dir=app_dir() / "plans", on_saved=self.check_config)
        except (ValueError, OSError, KeyError) as exc:
            self.log("课程方案管理器无法打开：" + type(exc).__name__)
            messagebox.showerror("无法管理方案", "请先检查本机课程配置是否有效。")

    def export_log(self) -> None:
        destination = filedialog.asksaveasfilename(
            title="导出本机运行日志", defaultextension=".txt",
            filetypes=[("文本文件", "*.txt")])
        if not destination:
            return
        try:
            # Only our own fixed, redacted status messages are present here.
            Path(destination).write_text(self.log_box.get("1.0", "end-1c"), encoding="utf-8")
        except OSError:
            messagebox.showerror("导出失败", "无法写入该文件，请检查目录权限。")

    def pick_config(self) -> None:
        name = filedialog.askopenfilename(title="选择配置文件", filetypes=[("YAML 文件", "*.yaml *.yml")])
        if name:
            self.path.set(name)
            self.check_config()

    def open_config(self) -> None:
        target = Path(self.path.get())
        if not target.is_file():
            messagebox.showerror("配置文件不存在", "请先选择真实存在的 YAML 配置文件")
            return
        if os.name == "nt":
            os.startfile(str(target))  # type: ignore[attr-defined]
        else:
            self.log("非 Windows 系统请使用文本编辑器编辑：" + target.name)

    def open_official_portal(self) -> None:
        """Open the published school portal in the system browser, not by HTTPX.

        No credentials or cookie are transferred from this application.
        """
        try:
            if not webbrowser.open(OFFICIAL_PORTAL):
                raise RuntimeError("default browser unavailable")
            self.log("已在默认浏览器中请求打开华水教务官网；请自行完成官方登录。")
        except Exception:
            messagebox.showerror("无法打开网站", "请手动在浏览器打开 https://jwmis.ncwu.edu.cn")

    def check_config(self) -> None:
        if self.worker:
            return
        try:
            config = read_config(Path(self.path.get()), offline=True)
            self.populate(config)
            self._previous_states = {}
            self._demo_report = None
            self._demo_index = 0
            self.status.set("配置结构检查通过｜真实接口尚需本人确认")
            self.log("配置结构通过；这不表示真实学校接口已验收。")
        except Exception as exc:
            self.status.set("配置检查失败：" + type(exc).__name__)
            self.log("配置检查失败，请检查 YAML 中缺失或非法的设置。")

    def preflight(self) -> None:
        if self.worker:
            return
        try:
            config = read_config(Path(self.path.get()), offline=True)
            checks = inspect_readiness(config)
        except Exception:
            messagebox.showerror("无法检查", "配置结构无效，请先修正 YAML 文件。")
            return
        blockers, warnings = split_checks(checks)
        messagebox.showinfo("启动前检查（不联网）", format_checks(checks))
        self.log(f"离线预检：{len(blockers)} 项阻止启动，{len(warnings)} 项待核对；没有连接学校。")

    def populate(self, config: dict) -> None:
        self.table.delete(*self.table.get_children())
        for item in config["courses"]:
            key = str(item["key"])
            course = str(item.get("course_name") or item.get("course_id") or "待填写")
            teacher = str(item.get("teacher") or "—")
            self.table.insert("", "end", iid=key, values=(key, course, teacher, "待监测"))

    def demo_next(self) -> None:
        """Step through 3 bundled fictional snapshots; never touch the school."""
        if self.worker:
            messagebox.showinfo("请先停止监测", "监测运行期间无法切换到离线示例。")
            return
        try:
            if self._demo_report is None or self._demo_index >= len(self._demo_report.steps):
                self._demo_report = run_demo(bundled_demo())
                self._demo_index = 0
                self.populate({"courses": self._demo_report.courses})
            current = self._demo_report.steps[self._demo_index]
            for key, value in current.states.items():
                if self.table.exists(key):
                    self.table.set(key, "status", value)
            self._demo_index += 1
            total = len(self._demo_report.steps)
            self.status.set(f"虚构离线样例｜快照 {current.number}/{total}｜没有连接学校")
            if current.newly_available:
                self.log(f"虚构样例第 {current.number} 步：发现 {len(current.newly_available)} 个目标由无余量变为有余量。")
            else:
                self.log(f"虚构样例第 {current.number} 步：未出现新的余量。")
            if current.number == total:
                self.log("三步虚构样例结束；再次点击可重新演示。样例结果不是实时数据。")
        except (ValueError, OSError, KeyError, AssertionError) as exc:
            self._demo_report = None
            self._demo_index = 0
            self.status.set("虚构样例无法读取：" + type(exc).__name__)
            self.log("内置离线样例读取失败；请重新安装完整软件包。")

    def audit_local_har(self) -> None:
        """Inspect a previously captured HAR locally without network or replay."""
        if self.worker:
            messagebox.showinfo("请先停止监测", "监测运行期间请勿切换离线分析。")
            return
        filename = filedialog.askopenfilename(title="选择本人已授权导出的 HAR（仅本地统计）",
                                              filetypes=[("HAR 文件", "*.har")])
        if not filename:
            return
        try:
            summary = summarize_har(Path(filename))
            messagebox.showinfo("HAR 本机结构统计（不代表接口已验证）", format_summary(summary))
            self.log(f"本机 HAR 结构统计完成：包含 {summary.matching_school_origin} 条华水 HTTPS 请求；未连接学校。")
        except (OSError, ValueError):
            messagebox.showerror("HAR 检查失败", "文件结构无效、文件过大或无法读取；请勿上传未经脱敏的 HAR。")

    def review_catalog_evidence(self) -> None:
        """Compare a locally redacted evidence pair; never enable live catalog mode."""
        if self.worker:
            messagebox.showinfo("请先停止监测", "监测期间请勿切换离线分析。")
            return
        filename = filedialog.askopenfilename(
            title="选择已脱敏的证据对照 JSON（不要选择原始 HAR）",
            filetypes=[("JSON 文件", "*.json")])
        if not filename:
            return
        outcome = inspect_local_file(Path(filename))
        status = outcome["status"]
        captions = {
            "blocked": "证据不完整或不安全，未通过离线对照",
            "needs_more_evidence": "证据尚不足，仍需更多观察",
            "ready_for_manual_review": "结构对照通过，但仍需本人对官方页面人工复核",
        }
        result = captions.get(status, "证据状态未知")
        self.status.set("离线证据对照｜" + result)
        self.log(f"本机证据对照：{outcome['matched_rows']} 条匿名记录一致；未连接学校。")
        reason = "、".join(outcome.get("reason_codes", ()))
        messagebox.showinfo(
            "只读证据对照（非实时）",
            f"{result}\n一致记录：{outcome['matched_rows']}\n"
            f"原因代码：{reason}\n\n"
            "不会自动将接口标记为已验证，更不会提交选课。"
        )

    def replay_local(self) -> None:
        if self.worker:
            return
        filename = filedialog.askopenfilename(title="选择已脱敏的本机课程 HTML", filetypes=[("HTML 文件", "*.html *.htm")])
        if not filename:
            return
        try:
            self._demo_report = None
            self._demo_index = 0
            config = read_config(Path(self.path.get()), offline=True)
            raw = _read_local_html(filename)
            # Schema diagnostics are deliberately not printed: only counts.
            hints = inspect_html(raw)
            courses = parse_courses(raw, config["protocol"])
            self.populate(config)
            for item in config["courses"]:
                matched = [c for c in courses if Scheduler.matches(item, c)]
                if len(matched) > 1:
                    state = "样本中多个班级匹配"
                elif not matched:
                    state = "样本中未匹配"
                else:
                    remain = matched[0].remain
                    state = "样本余量未知" if remain is None else f"样本余量 {remain}"
                self.table.set(str(item["key"]), "status", state)
            self.status.set(f"离线演练完毕｜解析 {len(courses)} 行，发现 {len(hints)} 个候选表格")
            self.log("完成本机脱敏 HTML 演练，没有连接学校、没有执行选课。")
        except Exception as exc:
            self.status.set("离线演练失败：" + type(exc).__name__)
            self.log("无法解析本机样本，请核对脱敏页面和表头列号。")

    def start_monitor(self) -> None:
        if self.worker:
            return
        try:
            # First run offline checks so placeholders and missing authorization
            # are explained before any attempt at a network connection.
            config = read_config(Path(self.path.get()), offline=True)
            checks = inspect_readiness(config)
            blockers, warnings = split_checks(checks)
            if blockers:
                self.status.set(f"无法启动｜{len(blockers)} 项本地前提未完成")
                messagebox.showwarning("先完成启动预检", format_checks(checks))
                self.log(f"启动被本地预检拦截：{len(blockers)} 项；没有连接学校。")
                return
            config = read_config(Path(self.path.get()), offline=False)
            config = resolve_state_file(config, app_dir())
            self.populate(config)
            self._previous_states = {}
            self._demo_report = None
            self._demo_index = 0
        except Exception as exc:
            self.status.set("无法启动监测：" + type(exc).__name__)
            self.log("真实模式未启用：官方接口参数、本人登录状态或权限尚未完成核验。")
            return
        warning_text = (f"本地预检发现 {len(warnings)} 项提示，请核对课程代码、节次与备选课程。\n\n"
                        if warnings else "")
        if not messagebox.askokcancel("确认只读操作", warning_text +
                                      "仅在学校允许的频率与访问方式下使用。\n程序只查询课程信息和提醒，不会提交选课。确认开始？"):
            return
        self.worker = MonitorWorker(config, self.events)
        self.worker.start()
        self.start_button.configure(state="disabled")
        self.edit_button.configure(state="disabled")
        self.plans_button.configure(state="disabled")
        self.config_entry.configure(state="disabled")
        self.pick_button.configure(state="disabled")
        self.open_button.configure(state="disabled")
        self.stop_button.configure(state="normal")
        self.status.set("正在初始化本人会话…")
        self.log("开始合规只读监测。")

    def stop_monitor(self) -> None:
        if self.worker:
            self.worker.stop()
            self.status.set("正在停止…")
            self.stop_button.configure(state="disabled")

    def poll(self) -> None:
        try:
            while True:
                event = self.events.get_nowait()
                if event.kind == "status":
                    cycles, states, request_ms, parse_ms = event.value
                    self.status.set(f"只读监测｜查询 {cycles} 次｜最近请求 {request_ms:.0f} ms｜解析 {parse_ms:.1f} ms｜请以官网为准")
                    newly_available = status_changes(self._previous_states, states)
                    if newly_available:
                        self.bell()
                        self.log(f"检测到 {len(newly_available)} 个目标课程有余量，请前往官网确认；不代表选课成功。")
                    self._previous_states = dict(states)
                    for key, state in states.items():
                        if self.table.exists(key):
                            self.table.set(key, "status", str(state))
                elif event.kind == "message":
                    self.log(str(event.value))
                elif event.kind == "stop_reason":
                    self.status.set("已停止｜" + str(event.value))
                    self.log("已停止｜" + str(event.value))
                elif event.kind == "error":
                    self.log("监测异常：" + str(event.value) + "；请重新核对会话、协议及访问权限。")
                    self.status.set("监测异常终止")
                elif event.kind == "finished":
                    self.worker = None
                    self.start_button.configure(state="normal")
                    self.edit_button.configure(state="normal")
                    self.plans_button.configure(state="normal")
                    self.config_entry.configure(state="normal")
                    self.pick_button.configure(state="normal")
                    self.open_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
                    self.log("监测任务已结束；未提交任何选课请求。")
                    if self._closing:
                        self.destroy()
                        return
        except queue.Empty:
            pass
        if not self._closing or self.worker:
            self.after(300, self.poll)

    def close(self) -> None:
        if self.worker:
            self._closing = True
            self.status.set("正在安全停止后台任务并关闭窗口…")
            self.worker.stop()
            return
        self.destroy()



def run_packaged_gui_smoke() -> None:
    """Exercise an actual Tk window without network or access to user settings.

    This is specifically intended for a Windows-hosted build runner. Unlike
    --self-test it verifies that the frozen EXE can initialize Tk and its
    packaged UI resources. It never opens the browser or authenticates.
    """
    from tempfile import TemporaryDirectory
    from unittest.mock import patch

    with TemporaryDirectory(prefix="kingo-gui-smoke-") as directory:
        with patch.object(sys.modules[__name__], "app_dir", return_value=Path(directory)):
            app = Application()
            try:
                app.withdraw()  # No visible desktop interference in CI.
                app.update()
                if len(app.table.get_children()) != 2:
                    raise AssertionError("Expected two placeholder courses in packaged template")
                with patch("core.client.httpx.AsyncClient", side_effect=AssertionError("network attempted")):
                    app.demo_next()
                    app.update()
                    if app.table.set("target-B", "status") != "虚构样例：已满":
                        raise AssertionError("First demo snapshot is invalid")
                    app.demo_next()
                    app.update()
                    if "发现余量" not in app.table.set("target-B", "status"):
                        raise AssertionError("Second demo snapshot is invalid")
                app.check_config()
                app.update()
                with patch.object(messagebox, "showwarning") as warning, \
                     patch.object(sys.modules[__name__], "MonitorWorker",
                                  side_effect=AssertionError("network attempt")):
                    app.start_monitor()
                warning.assert_called_once()
                if app.worker is not None:
                    raise AssertionError("Placeholder plan unexpectedly started monitoring")
                app.check_config()
                if app.table.set("A", "course") != "FILL_FROM_OFFICIAL_PAGE":
                    raise AssertionError("Demo did not restore the real local configuration")
            finally:
                app.destroy()


def main() -> int:
    # The packaged --self-test is for Windows CI and makes no school requests.
    # It avoids Tk/display startup to work in non-interactive build runners.
    if len(sys.argv) == 2 and sys.argv[1] == "--self-test":
        try:
            run_selftest(bundled_config(), bundled_demo())
            return 0
        except Exception:
            return 11
    if len(sys.argv) == 2 and sys.argv[1] == "--smoke-gui":
        try:
            run_packaged_gui_smoke()
            return 0
        except Exception:
            return 12
    if len(sys.argv) > 1:
        return 2
    # The CLI is preserved unchanged as main.py; this executable starts a UI.
    logger.remove()
    Application().mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
