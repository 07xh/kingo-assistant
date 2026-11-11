"""Standalone Windows desktop UI for KingoAssistant V5.1.

The current NCWU client remains read-only. The automatic-enrollment state
machine is offline-tested but has no verified school-specific adapter yet.
No CAPTCHA processing, rate-limit evasion or concurrent write bursts.
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
from core.desktop_settings import load_config, quick_copy_value, resolve_state_file, status_changes
from core.demo import DemoReport, run_demo
from desktop_editor import SettingsEditor
from desktop_plans import PlanManager
from desktop_tray import TrayController
from desktop_scheduled_worker import DesktopScheduledWorker, ScheduledWorkerEvent
from core.inspector import inspect_html
from core.har_audit import summarize_har, format_summary
from tools.catalog_evidence import inspect_local_file
from core.scheduler import Scheduler
from core.preflight import inspect_readiness, split_checks, format_checks
from core.offline_selftest import run_selftest
from core.offline_benchmark import format_benchmark, run_offline_benchmark
from core.official_portal_schedule import OfficialPortalAppointment, prepare_appointment, format_countdown
from core.session_probe import SessionProbeResult, probe_own_session
from core.official_entries import OFFICIAL_ENTRIES, probe_official_login_surfaces
from core.scheduled_ui_bridge import prepare_scheduled_plan
from core.enrollment_schedule import ScheduledRunReport
from core.time_sync import TimeSync
from main import _read_local_html, validate

APP_TITLE = "KingoAssistant · Windows（只读监测｜自动提交尚未接入）"
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
    if not offline:
        config = resolve_state_file(config, app_dir())
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
                                 client.url(self.config["protocol"]["identity_referer"]),
                                 budgeted_get=lambda url, ref: client.read("GET", url, ref))
            except Exception:
                self.updates.put(MonitorEvent("message", "校时不可用，使用本机时间。"))
            client.prepare_query(self.config["xnxq"])
            scheduler = Scheduler(client, clock, self.config)
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
                    await asyncio.sleep(0.5)
                await running
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


class Application(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("960x650")
        self.minsize(750, 520)
        self.events: queue.Queue[MonitorEvent] = queue.Queue()
        self._tray = TrayController(lambda kind: self.events.put(MonitorEvent(kind)))
        self.worker: MonitorWorker | None = None
        # No school submit adapter is shipped. An audited provider may inject
        # a factory later; local plan validation alone never arms execution.
        self._engine_factory = None
        self.scheduled_worker: DesktopScheduledWorker | None = None
        self._previous_states: dict[str, str] = {}
        self._display_courses: dict[str, dict] = {}
        self._demo_report: DemoReport | None = None
        self._demo_index = 0
        self._closing = False
        self._benchmark_in_progress = False
        self._entry_probe_in_progress = False
        self._official_portal_appointment: OfficialPortalAppointment | None = None
        self._portal_prelogin_opened = False
        self._session_check_in_progress = False
        self.portal_appointment_status = tk.StringVar(value="官网预约未启动（与自动提交无关）")
        self.path = tk.StringVar(value=str(ensure_config()))
        self.status = tk.StringVar(value="未启动｜本机离线检查可直接使用")
        self._build()
        menu = tk.Menu(self)
        manual_menu = tk.Menu(menu, tearoff=False)
        manual_menu.add_command(label="打开官方教务（手动）", command=self.open_official_portal)
        manual_menu.add_command(label="复制代码并打开官网（手动）", command=self.prepare_selected_course)
        manual_menu.add_command(label="显示/隐藏官网预约（备用）", command=self.toggle_portal_panel)
        menu.add_cascade(label="手动辅助", menu=manual_menu)
        tools_menu = tk.Menu(menu, tearoff=False)
        tools_menu.add_command(label="脱敏 HTML 离线演练", command=self.replay_local)
        tools_menu.add_command(label="虚构样例：下一步", command=self.demo_next)
        tools_menu.add_command(label="本机 HAR 结构检查（脱敏统计）", command=self.audit_local_har)
        tools_menu.add_command(label="选课目录证据对照（离线 JSON）", command=self.review_catalog_evidence)
        tools_menu.add_command(label="北化课表参数 · 离线调度测速", command=self.benchmark_offline)
        tools_menu.add_command(label="华水官方入口清单（离线）", command=self.show_official_entries)
        tools_menu.add_command(label="检测官方登录页可达性（手动）", command=self.probe_official_entries)
        menu.add_cascade(label="离线工具", menu=tools_menu)
        app_menu = tk.Menu(menu, tearoff=False)
        app_menu.add_command(label="显示/隐藏高级配置", command=self.toggle_advanced_panel)
        app_menu.add_command(label="最小化到系统托盘", command=self.close)
        app_menu.add_separator()
        app_menu.add_command(label="完全退出", command=self.quit_application)
        menu.add_cascade(label="程序", menu=app_menu)
        self.configure(menu=menu)
        self.after(300, self.poll)
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.check_config()

    def _build(self) -> None:
        top = ttk.Frame(self, padding=(16, 14))
        top.pack(fill="x")
        ttk.Label(top, text="KingoAssistant · Windows", font=("Microsoft YaHei UI", 16, "bold")).pack(anchor="w")
        ttk.Label(top, text="本机独立运行｜当前只读监测｜自动执行核心已加入，官方接口待验收", foreground="#666").pack(anchor="w", pady=(4, 0))

        cfg = ttk.Frame(self, padding=(16, 6))
        cfg.pack(fill="x")
        ttk.Label(cfg, text="课程和志愿：").pack(side="left")
        self.edit_button = ttk.Button(cfg, text="编辑课程和设置", command=self.edit_settings)
        self.edit_button.pack(side="left", padx=5)
        self.plans_button = ttk.Button(cfg, text="课程方案", command=self.manage_plans)
        self.plans_button.pack(side="left", padx=5)

        # Advanced protocol file controls remain available, but do not distract
        # from the read-only workflow or imply that YAML unlocks live enrollment.
        self.advanced_panel = ttk.LabelFrame(self, text="高级配置（仅本机）", padding=8)
        ttk.Label(self.advanced_panel, text="配置文件").pack(side="left")
        self.config_entry = ttk.Entry(self.advanced_panel, textvariable=self.path)
        self.config_entry.pack(side="left", fill="x", expand=True, padx=(8, 6))
        self.pick_button = ttk.Button(self.advanced_panel, text="选择…", command=self.pick_config)
        self.pick_button.pack(side="left")
        self.open_button = ttk.Button(self.advanced_panel, text="打开文件", command=self.open_config)
        self.open_button.pack(side="left", padx=5)

        self.action_panel = ttk.Frame(self, padding=(16, 8))
        self.action_panel.pack(fill="x")
        self.check_button = ttk.Button(self.action_panel, text="检查配置", command=self.check_config)
        self.check_button.pack(side="left", padx=(0, 8))
        ttk.Button(self.action_panel, text="运行准备度", command=self.preflight).pack(side="left", padx=(0, 8))
        self.start_button = ttk.Button(self.action_panel, text="启动只读监测", command=self.start_monitor)
        self.start_button.pack(side="left", padx=(0, 8))
        self.stop_button = ttk.Button(self.action_panel, text="停止", command=self.stop_monitor, state="disabled")
        self.stop_button.pack(side="left")
        self.scheduled_preflight_button = ttk.Button(
            self.action_panel, text="检查自动选课计划（离线）",
            command=self.inspect_scheduled_runner,
        )
        self.scheduled_preflight_button.pack(side="right")
        self.scheduled_start_button = ttk.Button(
            self.action_panel, text="自动执行（待接入官方接口）",
            command=self.start_scheduled_enrollment, state="disabled",
        )
        self.scheduled_start_button.pack(side="right", padx=(0, 8))
        self.scheduled_stop_button = ttk.Button(
            self.action_panel, text="取消自动计划",
            command=self.stop_scheduled_enrollment, state="disabled",
        )
        self.scheduled_stop_button.pack(side="right", padx=(0, 8))

        self.portal_panel = ttk.LabelFrame(self, text="手动官网备用（不自动提交）", padding=8)
        self.portal_appointment_button = ttk.Button(
            self.portal_panel, text="预约按时打开官网（辅助）",
            command=self.toggle_portal_appointment,
        )
        self.portal_appointment_button.pack(side="left", padx=(0, 8))
        ttk.Button(self.portal_panel, text="现在打开官网登录",
                   command=self.open_official_portal).pack(side="left", padx=(0, 8))
        self.session_check_button = ttk.Button(
            self.portal_panel, text="核验应用会话（只读）", command=self.check_script_session)
        self.session_check_button.pack(side="left", padx=(0, 8))
        ttk.Label(
            self.portal_panel, textvariable=self.portal_appointment_status,
            foreground="#666", wraplength=650,
        ).pack(side="left", fill="x", expand=True)

        ttk.Label(self, textvariable=self.status, padding=(16, 6)).pack(fill="x")
        frame = ttk.LabelFrame(self, text="目标课程（当前只读；未接入真实自动选课提交）", padding=8)
        self.course_frame = frame
        frame.pack(fill="both", expand=True, padx=16, pady=(2, 8))
        self.table = ttk.Treeview(frame, columns=("key", "course", "class_id", "teacher", "status"), show="headings")
        for col, label, width in (("key", "志愿", 70), ("course", "课程/代码", 220),
                                   ("class_id", "教学班", 110), ("teacher", "教师", 120),
                                   ("status", "状态", 250)):
            self.table.heading(col, text=label)
            self.table.column(col, width=width, stretch=True)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.table.pack(fill="both", expand=True)
        ttk.Label(frame, text="双击课程可复制代码。手动官网辅助与离线诊断已移至菜单，不属于自动提交。",
                  foreground="#666").pack(anchor="w", pady=(5, 3))
        self.table.bind("<Double-1>", lambda _event: self.copy_selected_field("course_id"))

        log_frame = ttk.LabelFrame(self, text="本地运行信息（不展示账号、Cookie 或网页原文）", padding=8)
        log_frame.pack(fill="both", expand=True, padx=16, pady=(0, 10))
        self.log_box = tk.Text(log_frame, height=7, state="disabled", wrap="word")
        self.log_box.pack(fill="both", expand=True)
        ttk.Button(log_frame, text="导出本机运行日志", command=self.export_log).pack(anchor="e", pady=(5, 0))

    def toggle_advanced_panel(self) -> None:
        if self.advanced_panel.winfo_manager():
            self.advanced_panel.pack_forget()
        else:
            self.advanced_panel.pack(fill="x", before=self.action_panel)

    def toggle_portal_panel(self) -> None:
        if self.portal_panel.winfo_manager():
            self.portal_panel.pack_forget()
        else:
            self.portal_panel.pack(fill="x", before=self.course_frame)

    def log(self, message: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", time.strftime("%H:%M:%S") + "  " + message + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def edit_settings(self) -> None:
        if self.worker or getattr(self, "scheduled_worker", None):
            messagebox.showinfo("请先停止监测", "运行期间无法修改配置，请先停止当前任务。")
            return
        try:
            SettingsEditor(self, Path(self.path.get()), self.check_config)
        except (ValueError, OSError, KeyError) as exc:
            self.log("配置编辑器无法启动：" + type(exc).__name__)
            messagebox.showerror("无法打开编辑器", "配置文件结构可能损坏，请先点击检查配置。")

    def manage_plans(self) -> None:
        if self.worker or getattr(self, "scheduled_worker", None):
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

    def copy_selected_field(self, field: str) -> bool:
        """Local clipboard helper; never log or transmit the copied field."""
        selected = self.table.selection()
        if not selected:
            messagebox.showinfo("请选择目标课程", "先在目标课程列表中选中一行。")
            return False
        if self._demo_report is not None:
            messagebox.showinfo("仅为虚构样例", "当前显示的是离线演示数据；先点击“检查配置”恢复真实目标。")
            return False
        target = self._display_courses.get(str(selected[0]))
        try:
            value = quick_copy_value(target, field)
            self.clipboard_clear()
            self.clipboard_append(value)
            self.update_idletasks()
        except ValueError as error:
            messagebox.showwarning("课程信息不完整", str(error))
            return False
        except tk.TclError:
            messagebox.showerror("无法复制", "系统剪贴板暂不可用，请检查后重试。")
            return False
        label = {"course_id": "课程代码", "class_id": "教学班编号",
                 "course_name": "课程名称"}.get(field, "信息")
        self.log(f"已复制所选课程的{label}到本机剪贴板；请在官网核对并手动提交。")
        return True

    def prepare_selected_course(self) -> None:
        """One deliberate action: copy exact course ID and launch official login page."""
        if self.copy_selected_field("course_id"):
            self.open_official_portal()

    def check_script_session(self) -> None:
        """Check only the application's own approved read-only identity session.

        The UI never imports browser cookies or sends school enrollment requests.
        Any permitted session check runs off the Tk main thread.
        """
        if (self.worker or getattr(self, "scheduled_worker", None)
                or self._session_check_in_progress or self._closing):
            return
        try:
            config = read_config(Path(self.path.get()), offline=True)
        except (ValueError, TypeError, KeyError, OSError):
            messagebox.showwarning("无法核验会话", "本机配置无效，请先修正。")
            return
        self._session_check_in_progress = True
        self.session_check_button.configure(state="disabled")
        self.status.set("正在核验应用会话｜只读操作，不会提交选课")

        def execute() -> None:
            try:
                result = asyncio.run(probe_own_session(config))
            except Exception:
                result = SessionProbeResult("unavailable", False)
            self.events.put(MonitorEvent("session_probe", result))

        try:
            threading.Thread(target=execute, name="KingoSessionProbe", daemon=True).start()
        except RuntimeError:
            self._session_check_in_progress = False
            self.session_check_button.configure(state="normal")
            self.status.set("会话核验线程无法启动；未请求学校")

    def inspect_scheduled_runner(self) -> None:
        """Validate the real runner's local admission contract, never start it."""
        if self.worker or getattr(self, "scheduled_worker", None):
            messagebox.showinfo("先停止监测", "运行期间无法变更或核对定时计划。")
            return
        try:
            config = read_config(Path(self.path.get()), offline=True)
            plan = prepare_scheduled_plan(config)
        except (ValueError, TypeError, KeyError, OSError) as exc:
            self.status.set("自动选课计划预检未通过｜没有启动后台任务")
            self.log("本地自动选课计划预检未通过；未连接学校。")
            messagebox.showwarning("定时计划字段待完善", str(exc))
            return
        self.status.set("定时计划本地字段已核对｜真实自动选课未启用")
        self.log("定时计划字段检查通过；学校课程目录、窗口和提交适配器仍未验收。")
        messagebox.showinfo(
            "本地定时计划检查完成（不是抢课启动）",
            f"计划时间（北京时间）：{plan.target_time}\n"
            f"精确课程及教学班：{len(plan.targets)} 个\n"
            f"窗口检查间隔：{plan.poll_interval_range[0]}–{plan.poll_interval_range[1]} 秒\n"
            f"最长等待窗口：{plan.max_wait_minutes} 分钟\n\n"
            "这只代表本地字段和调度器参数有效。"
            "当前未提供经过独立验收的华水课程目录、窗口、已选记录及正式提交适配器；"
            "因此没有启动自动选课，也没有创建学校网络请求。"
        )

    def install_verified_engine_factory(self, factory) -> None:
        """Internal integration seam for a *reviewed* school-specific adapter.

        This API never grants permission or marks capabilities as verified.
        EnrollmentEngine validates all account, policy and gateway gates in the
        background before the scheduler is allowed to wait.
        """
        if self.worker or self.scheduled_worker:
            raise RuntimeError("运行中的任务不允许切换选课适配器")
        if not callable(factory):
            raise TypeError("选课执行器必须由可信工厂构造")
        self._engine_factory = factory
        self.scheduled_start_button.configure(state="normal")

    def start_scheduled_enrollment(self) -> None:
        """Start a verified *in-process* engine; never open a browser."""
        if (self.worker or self.scheduled_worker
                or getattr(self, "_session_check_in_progress", False)):
            messagebox.showinfo("已有任务", "请先完成当前监测、身份核验或预约任务")
            return
        factory = self._engine_factory
        if factory is None:
            self.status.set("自动选课不可启动｜未接入经核验的华水官方提交适配器")
            self.log("没有真实学校选课接口适配器，未创建任何学校网络请求。")
            return
        try:
            config = read_config(Path(self.path.get()), offline=True)
            plan = prepare_scheduled_plan(config)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            self.status.set("自动选课计划未通过本地预检")
            self.log("计划缺少真实课程代码/教学班、学期或时间，不启动。")
            messagebox.showwarning("预约参数待完善", str(exc))
            return
        if not messagebox.askyesno(
            "启动应用内自动执行",
            f"选课时间（北京时间）：{plan.target_time}\n"
            f"确定的课程及教学班：{len(plan.targets)} 条\n\n"
            "此功能将通过经过授权的应用内教务适配器发送正式提交，"
            "只以官方已选课程记录核验结果；不使用浏览器代替提交。\n"
            "确认启动本次自动选课任务？",
        ):
            return
        # Prevent a previous manual browser appointment from firing in the
        # middle of an explicitly confirmed in-process enrollment run.
        if getattr(self, "_official_portal_appointment", None) is not None:
            self._official_portal_appointment = None
            self._portal_prelogin_opened = False
            self.portal_appointment_button.configure(text="预约按时打开官网（辅助）")
            self.portal_appointment_status.set("手动官网预约已取消；改用应用内执行")
            self.log("自动任务已取代同一窗口的手动官网预约。")
        sender = lambda item: self.events.put(
            MonitorEvent("scheduled_" + item.kind, item.value)
        )
        worker = DesktopScheduledWorker(plan, factory, sender)
        self.scheduled_worker = worker
        try:
            worker.start()
        except Exception:
            self.scheduled_worker = None
            self.status.set("后台计划启动失败")
            self.log("后台线程创建异常，未声称已提交课程。")
            return
        self.scheduled_start_button.configure(state="disabled")
        self.scheduled_stop_button.configure(state="normal")
        self.start_button.configure(state="disabled")
        self.edit_button.configure(state="disabled")
        self.plans_button.configure(state="disabled")
        if hasattr(self, "session_check_button"):
            self.session_check_button.configure(state="disabled")
        self.status.set("自动选课后台任务正在进行本地适配器核验")
        self.log("应用内后台执行器已启动；尚未向学校发出选课成功声明。")

    def stop_scheduled_enrollment(self) -> None:
        worker = self.scheduled_worker
        if worker is None:
            return
        worker.stop()
        self.scheduled_stop_button.configure(state="disabled")
        self.status.set("已请求取消｜若提交已发出，将完成官方结果核验后退出")
        self.log("已请求停止自动任务；不会强行中断不确定的选课写入。")

    def toggle_portal_appointment(self) -> None:
        """Schedule the official browser page only; never submit enrollment."""
        if getattr(self, "scheduled_worker", None) is not None:
            messagebox.showinfo("应用内任务正在运行",
                                "运行自动选课时不能另行启动官网预约。")
            return
        if self._official_portal_appointment is not None:
            self._official_portal_appointment = None
            self._portal_prelogin_opened = False
            self.portal_appointment_button.configure(text="预约按时打开官网（辅助）")
            self.portal_appointment_status.set("官网预约已取消")
            self.log("已取消本机官网预约；不会打开任何学校页面。")
            return
        try:
            config = read_config(Path(self.path.get()), offline=True)
            appointment = prepare_appointment(config, now_epoch=time.time())
        except (ValueError, KeyError, OSError) as exc:
            self.portal_appointment_status.set("官网预约未启动｜需补齐时间和真实课程")
            messagebox.showwarning("官网预约条件不满足", str(exc))
            return
        if not messagebox.askyesno(
            "确认预约官方入口（不是自动提交）",
            f"预约时间（北京时间）：{appointment.local_start}\n"
            f"已填写的真实课程志愿：{appointment.target_count} 门。\n\n"
            "注意：配置模板中的日期和时刻不是官方确认的开放时刻。"
            "请先核对学校针对本人年级及校区的最新专项通知。\n\n"
            "软件须保持运行：提前 15 分钟自动打开官网供本人登录，"
            "到点再次打开官网。不会自动登录或读取浏览器 Cookie。"
            "课程查找、提交和核验仍须本人在官方页面完成。\n\n"
            "确认你已核对准确时间并开始预约？",
        ):
            return
        self._official_portal_appointment = appointment
        self._portal_prelogin_opened = False
        self.portal_appointment_button.configure(text="取消官网预约")
        self.portal_appointment_status.set(
            f"官网预约：{appointment.local_start}（北京时间）；仅打开官方页面"
        )
        self.log("已预约按时打开官方教务入口；未联网检查，也不会自动提交。")

    def _tick_portal_appointment(self) -> None:
        """Called on Tk's event loop; no extra threads or school requests."""
        appointment = self._official_portal_appointment
        if appointment is None or self._closing:
            return
        if getattr(self, "scheduled_worker", None) is not None:
            self._official_portal_appointment = None
            self.portal_appointment_status.set("应用内执行期间已取消手动官网预约")
            return
        now = time.time()
        if appointment.is_due(now):
            self._official_portal_appointment = None  # at most one browser launch
            self.portal_appointment_button.configure(text="预约按时打开官网（辅助）")
            self.portal_appointment_status.set(
                "已到预约时间，已尝试打开官网；请在官方页面确认选课"
            )
            self.bell()
            self.open_official_portal()
            self.log("计划时刻已到；仅打开官方页面，不代表成功选课。")
            return
        if not self._portal_prelogin_opened and appointment.prelogin_is_due(now):
            self._portal_prelogin_opened = True  # one early browser launch at most
            self.open_official_portal()
            self.log("提前登录时段已到；打开官网不代表已经登录。")
        remaining = appointment.seconds_remaining(now)
        prelogin = "｜已尝试提前打开官网登录" if self._portal_prelogin_opened else ""
        self.portal_appointment_status.set(
            f"官网预约倒计时：{format_countdown(remaining)}{prelogin}"
            "｜须保持程序运行，按官方通知核对开放时刻"
        )

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
        if self.worker or getattr(self, "scheduled_worker", None):
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
        if self.worker or getattr(self, "scheduled_worker", None):
            return
        try:
            config = read_config(Path(self.path.get()), offline=True)
            checks = inspect_readiness(resolve_state_file(config, app_dir()))
        except Exception:
            messagebox.showerror("无法检查", "配置结构无效，请先修正 YAML 文件。")
            return
        blockers, warnings = split_checks(checks)
        messagebox.showinfo("启动前检查（不联网）", format_checks(checks))
        self.log(f"离线预检：{len(blockers)} 项阻止启动，{len(warnings)} 项待核对；没有连接学校。")

    def populate(self, config: dict) -> None:
        self.table.delete(*self.table.get_children())
        self._display_courses = {}
        for item in config["courses"]:
            key = str(item["key"])
            self._display_courses[key] = dict(item)
            course = str(item.get("course_name") or item.get("course_id") or "待填写")
            class_id = str(item.get("class_id") or "—")
            teacher = str(item.get("teacher") or "—")
            self.table.insert("", "end", iid=key, values=(key, course, class_id, teacher, "待监测"))

    def demo_next(self) -> None:
        """Step through 3 bundled fictional snapshots; never touch the school."""
        if self.worker or getattr(self, "scheduled_worker", None):
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

    def show_official_entries(self) -> None:
        """Display documented login surfaces without creating any HTTP requests."""
        lines = []
        for entry in OFFICIAL_ENTRIES:
            address = entry.launch_url or "在手机上使用官方喜鹊儿 App"
            lines.append(f"{entry.name}\n{address}")
        messagebox.showinfo(
            "华水官方入口（无联网）",
            "\n\n".join(lines) +
            "\n\n登录方式不是独立选课服务器的证据。"
            "当前只有网页教务入口可能成为已核验课程查询源。"
        )
        self.log("已查看华水官方入口清单；无网络请求。")

    def probe_official_entries(self) -> None:
        """One opt-in, unauthenticated GET per web login surface in a worker."""
        if self.worker or getattr(self, "scheduled_worker", None) or self._entry_probe_in_progress:
            messagebox.showinfo("入口检测", "请先结束监测或当前入口检测。")
            return
        if not messagebox.askyesno(
            "手动检测官网登录页",
            "仅在核实并提供了对应域名的请求预算时，才允许执行单次 HTTPS GET。"
            "当前版本未配置入口探测预算，将跳过联网请求；不读取课程，不提交选课。"
            "\n\n仅用于比较登录页可达性，不代表选课接口可用。确认执行？"
        ):
            return
        self._entry_probe_in_progress = True
        self.status.set("正在手动检测官方登录页｜不是课程查询")
        self.log("官网登录页检测已启动；无已核实域名预算时不联网。")

        def execute() -> None:
            try:
                result = asyncio.run(probe_official_login_surfaces())
                self.events.put(MonitorEvent("official_probe_done", result))
            except Exception:
                self.events.put(MonitorEvent("official_probe_error"))

        threading.Thread(
            target=execute, name="KingoOfficialLoginProbe", daemon=True
        ).start()

    def audit_local_har(self) -> None:
        """Inspect a previously captured HAR locally without network or replay."""
        if self.worker or getattr(self, "scheduled_worker", None):
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
        if self.worker or getattr(self, "scheduled_worker", None):
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

    def benchmark_offline(self) -> None:
        """Run a synthetic timing comparison in another thread, never campus HTTP."""
        if self.worker or getattr(self, "scheduled_worker", None) or self._benchmark_in_progress:
            messagebox.showinfo("离线调度测速", "请先结束当前监测或测速任务。")
            return
        self._benchmark_in_progress = True
        self.status.set("北化课表参数离线对标中｜未连接学校")
        self.log("离线对标启动：1.5 秒/工作线程、2 工作线程；不联网。")

        def execute() -> None:
            try:
                result = asyncio.run(run_offline_benchmark())
                self.events.put(MonitorEvent("benchmark_done", result))
            except Exception:
                self.events.put(MonitorEvent("benchmark_error"))

        threading.Thread(target=execute, name="KingoOfflineBenchmark",
                         daemon=True).start()

    def replay_local(self) -> None:
        if self.worker or getattr(self, "scheduled_worker", None):
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
        if self.worker or getattr(self, "scheduled_worker", None) or self._benchmark_in_progress:
            return
        try:
            # First run offline checks so placeholders and missing authorization
            # are explained before any attempt at a network connection.
            config = read_config(Path(self.path.get()), offline=True)
            # One exact snapshot for preflight and the running client. Avoid
            # a second YAML read between authorization and worker construction.
            config = resolve_state_file(config, app_dir())
            checks = inspect_readiness(config)
            blockers, warnings = split_checks(checks)
            if blockers:
                self.status.set(f"无法启动｜{len(blockers)} 项本地前提未完成")
                messagebox.showwarning("先完成启动预检", format_checks(checks))
                self.log(f"启动被本地预检拦截：{len(blockers)} 项；没有连接学校。")
                return
            validate(config, check_only=False)
            self.populate(config)
            self._previous_states = {}
            self._demo_report = None
            self._demo_index = 0
        except Exception as exc:
            self.status.set("无法启动监测：" + type(exc).__name__)
            self.log("真实模式未启用：官方接口参数、本人登录状态或权限尚未完成核验。")
            return
        # The explicit "启动只读监测" click is the opt-in for this run.
        # Advisory hints are logged and remain available via readiness UI.
        if warnings:
            self.log(f"本地预检有 {len(warnings)} 项非阻断提示；可在运行准备度中查看。")
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
        self._tick_portal_appointment()
        try:
            while True:
                event = self.events.get_nowait()
                if event.kind == "tray_restore":
                    self.restore_window()
                elif event.kind == "tray_quit":
                    self.quit_application()
                    if not self.worker and not getattr(self, "scheduled_worker", None):
                        return
                elif event.kind == "tray_error":
                    self.restore_window()
                    self.log("系统托盘不可用，主窗口已恢复；任务未被静默终止。")
                elif event.kind == "status":
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
                elif event.kind == "official_probe_done":
                    self._entry_probe_in_progress = False
                    results = event.value
                    names = {entry.identifier: entry.name for entry in OFFICIAL_ENTRIES}
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
                    self.status.set("官网登录页检测完毕｜未验证课程查询接口")
                    self.log("本次官网登录页检测结束；不代表本人已登录或课程余量可读取。")
                    messagebox.showinfo(
                        "官方登录页可达性（非选课接口）",
                        "\n".join(lines) +
                        "\n\n登录页响应速度不能用于证明选课后端速度或名额。"
                    )
                elif event.kind == "official_probe_error":
                    self._entry_probe_in_progress = False
                    self.status.set("官网登录页检测失败｜不影响正常监测配置")
                    self.log("官方入口检测未完成；没有启动选课任务。")
                elif event.kind == "session_probe":
                    self._session_check_in_progress = False
                    self.session_check_button.configure(state="normal")
                    result = event.value
                    self.status.set(result.message)
                    self.log(result.message)
                elif event.kind == "benchmark_done":
                    self._benchmark_in_progress = False
                    result = event.value
                    self.status.set("离线对标完成｜未请求学校接口")
                    self.log(f"本机离线测速完成：{result.started}/{result.finished} 模拟任务；"
                             f"平均调度间隔 {result.mean_interval_ms:.1f} ms。")
                    messagebox.showinfo("北化课表参数 · 离线调度测速",
                                        format_benchmark(result))
                elif event.kind == "benchmark_error":
                    self._benchmark_in_progress = False
                    self.status.set("离线对标失败｜未请求学校接口")
                    self.log("离线调度测速发生内部错误；未连接学校。")
                elif event.kind == "message":
                    self.log(str(event.value))
                elif event.kind == "stop_reason":
                    self.status.set("已停止｜" + str(event.value))
                    self.log("已停止｜" + str(event.value))
                elif event.kind == "error":
                    self.log("监测异常：" + str(event.value) + "；请重新核对会话、协议及访问权限。")
                    self.status.set("监测异常终止")
                elif event.kind == "scheduled_waiting":
                    self.status.set("自动选课任务：已通过本地权限校验，后台预约等待中")
                    self.log("已进入应用内定时执行流程；尚未代表学校选课成功。")
                elif event.kind == "scheduled_result":
                    outcome = event.value
                    if isinstance(outcome, ScheduledRunReport):
                        descriptions = {
                            "engine_completed": "自动执行结束｜按官方已选记录核验结果",
                            "engine_halted": "选课引擎保护性停止｜请核对官方已选记录",
                            "window_timeout": "等待官方选课窗口超时｜未提交选课",
                            "window_unknown": "无法确认官方开放状态｜已停止",
                            "cancelled_before_start": "预约尚未到点已取消",
                            "cancelled_waiting_for_window": "等待窗口期间已取消",
                            "invalid_poll_interval": "请求窗口本地策略无效｜已停止",
                        }
                        label = descriptions.get(outcome.reason, "自动选课任务已结束｜结果待确认")
                        self.status.set(label)
                        self.log(label + "；请以真实教务记录为准。")
                    elif outcome == "cancelled_before_start":
                        self.status.set("自动计划已取消｜未请求学校接口")
                    else:
                        self.status.set("自动计划结果无法确认｜停止进一步操作")
                elif event.kind == "scheduled_blocked":
                    self.status.set("自动选课未启动｜接口或身份授权条件未通过")
                    self.log("执行器校验失败；未进入预约等待。")
                elif event.kind == "scheduled_cleanup_warning":
                    self.log("教务适配器资源清理产生本地告警：" + str(event.value))
                elif event.kind == "scheduled_error":
                    self.status.set("自动选课任务异常终止")
                    self.log("自动执行器发生本地异常：" + str(event.value))
                elif event.kind == "scheduled_finished":
                    self.scheduled_worker = None
                    self.scheduled_stop_button.configure(state="disabled")
                    self.scheduled_start_button.configure(
                        state="normal" if self._engine_factory else "disabled"
                    )
                    self.start_button.configure(state="normal")
                    self.edit_button.configure(state="normal")
                    self.plans_button.configure(state="normal")
                    if (hasattr(self, "session_check_button")
                            and not self._session_check_in_progress):
                        self.session_check_button.configure(state="normal")
                    if self._closing and not self.worker:
                        self.destroy()
                        return
                elif event.kind == "finished":
                    self.worker = None
                    self.start_button.configure(state="normal")
                    self.edit_button.configure(state="normal")
                    self.plans_button.configure(state="normal")
                    self.config_entry.configure(state="normal")
                    self.pick_button.configure(state="normal")
                    self.open_button.configure(state="normal")
                    self.stop_button.configure(state="disabled")
                    if not self._closing:
                        if event.value == "stopped":
                            self.status.set("已停止｜后台只读监测已结束")
                        elif event.value == "completed":
                            self.status.set("已完成｜本次只读监测已结束")
                        # A prior stop_reason/error remains visible when failed.
                    self.log("监测任务已结束；未提交任何选课请求。")
                    if self._closing and not getattr(self, "scheduled_worker", None):
                        self.destroy()
                        return
        except queue.Empty:
            pass
        if not self._closing or self.worker or getattr(self, "scheduled_worker", None):
            self.after(300, self.poll)

    def restore_window(self) -> None:
        self.deiconify()
        self.lift()

    def close(self) -> None:
        """Hide to native tray without canceling any existing monitor/appointment."""
        if self._closing:
            return
        if self._tray.start():
            self.withdraw()
            self.status.set("已最小化到系统托盘；当前仅支持只读监测与官网备用预约")
        else:
            # Never leave a running process without a way to restore its window.
            self.iconify()

    def quit_application(self) -> None:
        """Only an explicit quit stops background work and the appointment."""
        if self._closing:
            return
        self._closing = True
        self._official_portal_appointment = None
        self._portal_prelogin_opened = False
        self._tray.stop()
        if self.worker:
            self.worker.stop()
        scheduled = getattr(self, "scheduled_worker", None)
        if scheduled:
            scheduled.stop()
        if self.worker or scheduled:
            self.status.set("正在安全结束后台任务并退出…")
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
                with patch.object(messagebox, "showinfo") as entries_dialog:
                    app.show_official_entries()
                    entries_dialog.assert_called_once()
                    if "喜鹊儿" not in str(entries_dialog.call_args.args):
                        raise AssertionError("Official entry inventory missing mobile app")
                if len(app.table.get_children()) != 2:
                    raise AssertionError("Expected two placeholder courses in packaged template")
                # Regression for PR #19/#20 GUI composition: main previously
                # referenced a deleted 'portal_row' frame and could not open.
                if app.scheduled_preflight_button.winfo_manager() != "pack":
                    raise AssertionError("Typed plan preflight entry not mounted in main UI")
                if str(app.scheduled_start_button.cget("state")) != "disabled":
                    raise AssertionError("Unverified live enrollment must never be enabled")
                if app.portal_panel.winfo_manager():
                    raise AssertionError("Manual-only fallback must stay hidden by default")
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
                app.table.selection_set("A")
                with patch("tkinter.messagebox.showwarning"):
                    if app.copy_selected_field("course_id"):
                        raise AssertionError("Placeholder course must not be copied")
                # A synthetic target is only placed in memory; no school network
                # or user config file is accessed or modified.
                app._display_courses["A"]["course_id"] = "SYNTHETIC101"
                if not app.copy_selected_field("course_id"):
                    raise AssertionError("Valid local course code was not copied")
                if app.clipboard_get() != "SYNTHETIC101":
                    raise AssertionError("Clipboard did not contain the selected course code")
                with patch("webbrowser.open", return_value=True) as opened:
                    app.prepare_selected_course()
                    opened.assert_called_once_with(OFFICIAL_PORTAL)

                # Exercise the packaged, offline-only benchmark through the
                # real GUI event queue without visiting school endpoints.
                from core.offline_benchmark import run_offline_benchmark as real_benchmark

                async def short_offline_benchmark():
                    return await real_benchmark(
                        interval_seconds=0.02,
                        workers=2,
                        cycles_per_worker=3,
                        simulated_processing_ms=35,
                    )

                with patch("httpx.AsyncClient", side_effect=AssertionError("benchmark used HTTP")):
                    with patch.object(sys.modules[__name__], "run_offline_benchmark",
                                      side_effect=short_offline_benchmark):
                        with patch("tkinter.messagebox.showinfo") as dialogs:
                            app.benchmark_offline()
                            if not app._benchmark_in_progress:
                                raise AssertionError("GUI benchmark did not start")
                            deadline = time.monotonic() + 3.0
                            while app._benchmark_in_progress and time.monotonic() < deadline:
                                app.update()
                                time.sleep(0.02)
                            if app._benchmark_in_progress:
                                raise AssertionError("GUI benchmark did not finish")
                            dialogs.assert_called_once()
                            report = str(dialogs.call_args.args[1])
                            if ("模拟启动/完成：6/6" not in report or
                                    "没有发起 HTTP 请求" not in report or
                                    "并非 HTTP RPS" not in report):
                                raise AssertionError("Packaged benchmark report is missing")
                            if app.status.get() != "离线对标完成｜未请求学校接口":
                                raise AssertionError("GUI benchmark did not report completion")
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
