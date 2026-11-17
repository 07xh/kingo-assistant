"""Standalone Windows desktop UI for KingoAssistant V5.1.

The current NCWU client remains read-only. The automatic-enrollment state
machine is offline-tested but has no verified school-specific adapter yet.
No CAPTCHA processing, rate-limit evasion or concurrent write bursts.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import queue
import sys
import threading
import time
import tkinter as tk
import webbrowser
from urllib.parse import urlsplit
from tkinter import filedialog, messagebox, simpledialog, ttk

from loguru import logger


from core.client import parse_courses
from core.desktop_settings import quick_copy_value, resolve_state_file, status_changes
from core.demo import DemoReport, run_demo
from desktop_editor import SettingsEditor
from desktop_plans import PlanManager
from desktop_tray import TrayController
from windows_config_runtime import (
    app_dir, bundled_config, bundled_demo, ensure_user_config, load_validated_config,
)
from windows_monitor_runtime import MonitorEvent, MonitorWorker
from windows_monitor_events import (
    apply_monitor_status, format_official_probe_results, scheduled_reason_text,
)
from windows_readonly_widgets import _open_readonly_popup, _create_readonly_tree
from windows_page_sections import build_manual_page, build_tools_page
from desktop_scheduled_worker import DesktopScheduledWorker, ScheduledWorkerEvent
from core.inspector import inspect_html
from core.har_audit import summarize_har, format_summary
from core.auth_route_audit import summarize_auth_route_har
from tools.catalog_evidence import inspect_local_file
from core.preflight import inspect_readiness, split_checks, format_checks
from core.offline_selftest import run_selftest
from core.offline_benchmark import format_benchmark, run_offline_benchmark
from core.official_portal_schedule import OfficialPortalAppointment, prepare_appointment, format_countdown
from core.session_probe import SessionProbeResult, probe_own_session
from core.native_preparation import NativePreparationResult, prepare_native_api
from core.native_timetable import TimetableReadResult, read_known_timetable
from core.known_school_pages import KnownSchoolPageResult, read_known_school_page, validate_known_page_contract
from core.ncwu_term_probe import TermProbeResult, probe_known_year_term, validate_term_contract
from core.native_login import NativeLoginResult, login_with_verified_form, validate_login_contract
from core.ncwu_signed_login import login_ncwusigned, validate_ncwulogin_entry
from core.ncwu_elective_table import inspect_elective_table
from core.ncwu_live_elective import LiveElectiveSnapshot, live_s202_snapshot
from core.ncwu_live_target_review import compare_live_targets
from core.ncwu_s202_readback import S202ReadbackResult, read_s202_selected_results
from core.ncwu_s202_round import S202RoundStatus, probe_s202_round
from core.ncwu_s202_score import PersonalScoreCount, read_personal_s202_score
from core.ncwu_course_plan import PlanSnapshot, read_current_course_plan
from core.ncwu_plan_alignment import compare_plan_targets
from core.ncwu_contract_capture import inspect_two_endpoint_contracts
from core.official_entries import OFFICIAL_ENTRIES, probe_official_login_surfaces
from core.scheduled_ui_bridge import prepare_scheduled_plan
from core.attestation import AttestationError, AttestationRegistry, resolve_registry
from core.latency import DEFAULT_LATENCY_FILE, summarize_file
from core.enrollment_schedule import ScheduledRunReport
from main import _read_local_html, validate

APP_TITLE = "KingoAssistant · Windows（只读监测｜自动提交尚未接入）"
OFFICIAL_PORTAL = "https://jwmis.ncwu.edu.cn"  # Confirmed by school notices; NOT an API endpoint.


def scoped_native_cookie(app, config: dict) -> str | None:
    """Never send a volatile login token to a different configured origin."""
    cookie = app.__dict__.get("_native_session_cookie")
    if cookie is None:
        return None  # Legacy external session behavior if no native login exists.
    origin = app.__dict__.get("_native_session_origin")
    address = config.get("base_url") if isinstance(config, dict) else None
    try:
        parsed = urlsplit(address)
    except (TypeError, ValueError):
        return ""
    if parsed.scheme == "https" and parsed.netloc == origin:
        return cookie
    return ""  # Explicitly prevent fallback to a legacy environment session.


def current_attestations() -> AttestationRegistry:
    """Read the evidence registry for the four write gates.

    A missing file yields an empty registry (every gate falls back to "not
    verified"); a corrupt file also yields an empty registry so the UI can still
    start. Neither case may ever enable a write.
    """
    try:
        return resolve_registry(None, app_dir=app_dir())
    except AttestationError as error:
        logger.warning("验收登记表不可用：{}", type(error).__name__)
        return AttestationRegistry(entries={}, root=app_dir())


def ensure_config() -> Path:
    """Idempotent first-run template setup; existing config is never replaced."""
    return ensure_user_config(app_dir(), bundled_config())


def read_config(path: Path, *, offline: bool = False) -> dict:
    """Keep original call signature and validation behavior for all UI actions."""
    return load_validated_config(
        path, offline=offline,
        user_directory=app_dir() if not offline else None,
        validator=validate,
    )


# Native S202 and legacy page/term/timetable read actions share the worker lifecycle.
# This helper does not inspect credentials or alter any source-specific
# result/event schema, so existing headless tests can still invoke methods
# with a minimal SimpleNamespace instead of a real Tk root.
_NCWU_READ_FLAGS = (
    "_live_catalog_in_progress",
    "_s202_round_probe_in_progress",
    "_s202_result_in_progress",
    "_s202_score_in_progress",
    "_course_plan_in_progress",
)


def _ncwu_read_busy(app, *, include_polling: bool = False) -> bool:
    return bool(
        app.worker or app.__dict__.get("scheduled_worker")
        or app._closing or app._native_login_in_progress
        or app._native_preparation_in_progress
        or app._session_check_in_progress
        or any(app.__dict__.get(flag, False) for flag in _NCWU_READ_FLAGS)
        or (include_polling and app.__dict__.get("_live_polling", False))
    )


def _start_ncwu_read_worker(
    app, *, config: dict, cookie: str | None, read_call,
    fallback_factory, flag: str, button, status_var,
    running_message: str, failure_message: str,
    event_kind: str, thread_name: str,
) -> None:
    """Single no-browser background job with Tk state restored on failure.

    The source-specific method checks session and configuration *before*
    calling here. The daemon posts only typed results; the Tk event loop
    owns UI updates and expired-session cleanup.
    """
    setattr(app, flag, True)
    button.configure(state="disabled")
    status_var.set(running_message)

    def execute() -> None:
        try:
            result = asyncio.run(read_call(config, session_cookie=cookie))
        except Exception:
            result = fallback_factory("unavailable")
        app.events.put(MonitorEvent(event_kind, result))

    try:
        threading.Thread(
            target=execute, name=thread_name, daemon=True,
        ).start()
    except RuntimeError:
        setattr(app, flag, False)
        button.configure(state="normal")
        status_var.set(failure_message)


def _finish_ncwu_read_event(
    app, result, *, flag: str, button, status_var,
    log_message: str, expired_message: str,
) -> None:
    """One Tk-thread completion path for four read-only NCWU screens."""
    setattr(app, flag, False)
    button.configure(state="normal")
    status_var.set(result.message)
    app.status.set(result.message)
    app.log(log_message)
    if result.code == "expired":
        app._native_session_cookie = None
        app._native_session_origin = None
        app.native_login_status.set(expired_message)
        if app.__dict__.get("_live_polling", False):
            app.stop_live_polling()


class Application(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1040x780")
        self.minsize(860, 640)
        self.events: queue.Queue[MonitorEvent] = queue.Queue()
        self._tray = TrayController(lambda kind: self.events.put(MonitorEvent(kind)))
        self.worker: MonitorWorker | None = None
        # No school submit adapter is shipped. An audited provider may inject
        # a factory later; local plan validation alone never arms execution.
        self._engine_factory = None
        # Evidence-backed write gates. Gateway flags alone are no longer enough:
        # each capability also needs a hash-verified, unexpired attestation.
        self.attestations = current_attestations()
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
        self._native_preparation_in_progress = False
        self._timetable_read_in_progress = False
        self._school_page_read_in_progress = False
        self._term_probe_in_progress = False
        self._native_login_in_progress = False
        self._live_catalog_in_progress = False
        self._s202_result_in_progress = False
        self._s202_round_probe_in_progress = False
        self._s202_score_in_progress = False
        self._course_plan_in_progress = False
        self._live_polling = False
        self._live_poll_timer = None
        self._live_catalog_popup = None
        self._live_catalog_tree = None
        self._live_catalog_message = None
        self._live_target_report = None
        self._live_target_message = None
        self._live_target_review_button = None
        self._native_session_cookie: str | None = None
        self._native_session_origin: str | None = None
        self.native_login_status = tk.StringVar(value="尚未登录｜已接入普通网页账号的签名协议，需本人实机验收")
        self.native_preparation_status = tk.StringVar(value="尚未通过 EXE 接口核验身份与课程目录")
        self.live_catalog_status = tk.StringVar(value="实时 S202 查询：先登录本人账号，再点击读取；选课未开放也可查询页面")
        self.s202_result_status = tk.StringVar(value="S202 官方已选结果页尚未读取；只读检查不能直接确认选课成功")
        self.s202_round_status = tk.StringVar(value="S202 本轮次尚未核对；选课未开放也可读取官方时间窗信息")
        self.s202_score_status = tk.StringVar(value="S202 已选门数尚未核对；汇总数量不代表目标教学班已选中")
        self.course_plan_status = tk.StringVar(value="开课计划尚未读取；与正式正选列表和实时余量是不同数据源")
        self.timetable_read_status = tk.StringVar(value="此前 HAR 已确认课表 JSON 路径；请求参数和 EXE 会话待核验")
        self.school_page_status = tk.StringVar(value="旧 HAR 已确认网页地址；已选结果的动态查询参数仍待核验")
        self.term_probe_status = tk.StringVar(value="11月3日及14日 HAR 均观察到无需表单参数的学年学期接口")
        self.portal_appointment_status = tk.StringVar(value="官网预约未启动（与自动提交无关）")
        self.path = tk.StringVar(value=str(ensure_config()))
        self.status = tk.StringVar(value="未启动｜本机离线检查可直接使用")
        self._build()
        menu = tk.Menu(self)
        manual_menu = tk.Menu(menu, tearoff=False)
        manual_menu.add_command(label="切换到手动官网备用", command=self.toggle_portal_panel)
        menu.add_cascade(label="手动备用", menu=manual_menu)
        menu.add_command(label="工具与日志", command=lambda: self.notebook.select(self.tab_tools))
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
        """Three clearly separated user journeys. Preserve legacy callbacks."""
        style = ttk.Style(self)
        style.configure("KingoTitle.TLabel", font=("Microsoft YaHei UI", 17, "bold"))
        style.configure("KingoStep.TLabelframe.Label",
                        font=("Microsoft YaHei UI", 10, "bold"))

        header = ttk.Frame(self, padding=(16, 12))
        header.pack(fill="x")
        ttk.Button(
            header, text="功能说明", command=self.show_function_guide,
        ).pack(side="right", padx=(10, 0))
        ttk.Label(header, text="KingoAssistant · 选课助手",
                  style="KingoTitle.TLabel").pack(anchor="w")
        ttk.Label(
            header,
            text="当前版本：EXE 原生登录已接入（待本人验证）；课程目录与自动提交尚未接通",
            foreground="#965b16",
        ).pack(anchor="w", pady=(4, 0))

        # Allocate the persistent status footer before the expanding
        # Notebook so it never gets squeezed out on smaller Windows screens.
        self.status_bar = ttk.Label(
            self, textvariable=self.status, foreground="#3b4757",
            padding=(16, 7), wraplength=990,
        )
        self.status_bar.pack(side="bottom", fill="x")

        self.notebook = ttk.Notebook(self)
        self.tab_home = ttk.Frame(self.notebook)
        self.tab_manual = ttk.Frame(self.notebook)
        self.tab_tools = ttk.Frame(self.notebook)
        self.notebook.add(self.tab_home, text="① 选课首页")
        self.notebook.add(self.tab_manual, text="② 手动官网备用")
        self.notebook.add(self.tab_tools, text="③ 工具与日志")
        self.notebook.pack(fill="both", expand=True, padx=10, pady=(0, 4))

        # Home: explain the actual workflow instead of displaying all buttons
        # as equal options in one toolbar.
        intro = ttk.Frame(self.tab_home, padding=(10, 6))
        intro.pack(fill="x")
        ttk.Label(
            intro, text="建议顺序：填写目标课程 → 软件内登录 → 检查本人课程",
            font=("Microsoft YaHei UI", 10, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            intro,
            text="第 1 步可离线使用；EXE 登录等待本人实测，课程目录和自动选课尚未接通。",
            foreground="#965b16",
        ).pack(anchor="w", pady=(2, 0))

        setup = ttk.LabelFrame(
            self.tab_home, text="第 1 步  ·  课程与志愿（本地可用）",
            padding=(10, 6), style="KingoStep.TLabelframe",
        )
        setup.pack(fill="x", padx=10, pady=(2, 5))
        self.edit_button = ttk.Button(setup, text="填写 / 修改目标课程",
                                      command=self.edit_settings)
        self.edit_button.pack(side="left", padx=(0, 8))
        self.plans_button = ttk.Button(setup, text="管理课程方案",
                                       command=self.manage_plans)
        self.plans_button.pack(side="left", padx=(0, 10))
        ttk.Label(
            setup, text="只编辑本机计划，不会向学校提交选课。",
            foreground="#666",
        ).pack(side="left")

        auth_panel = ttk.LabelFrame(
            self.tab_home, text="第 2 步  ·  软件内登录（协议已接入 · 待实机验收）",
            padding=(10, 6), style="KingoStep.TLabelframe",
        )
        auth_panel.pack(fill="x", padx=10, pady=(0, 5))
        auth_actions = ttk.Frame(auth_panel)
        auth_actions.pack(fill="x")
        self.native_login_button = ttk.Button(
            auth_actions, text="在 EXE 登录本人教务账号", command=self.start_native_login)
        self.native_login_button.pack(side="left", padx=(0, 8))
        self.native_prepare_button = ttk.Button(
            auth_actions, text="检查登录与课程数据（只读）",
            command=self.check_native_preparation)
        self.native_prepare_button.pack(side="left", padx=(0, 8))
        self.native_clear_button = ttk.Button(
            auth_actions, text="清除登录状态", command=self.clear_native_session)
        self.native_clear_button.pack(side="left")
        ttk.Label(
            auth_panel, textvariable=self.native_login_status,
            foreground="#666", wraplength=920,
        ).pack(anchor="w", pady=(6, 1))
        ttk.Label(
            auth_panel, textvariable=self.native_preparation_status,
            foreground="#666", wraplength=920,
        ).pack(anchor="w")
        timetable_row = ttk.Frame(auth_panel)
        timetable_row.pack(fill="x", pady=(5, 0))
        self.timetable_read_button = ttk.Button(
            timetable_row, text="读取本人课表（既有接口 · 只读）",
            command=self.check_known_timetable)
        self.timetable_read_button.pack(side="left", padx=(0, 8))
        self.live_catalog_button = ttk.Button(
            timetable_row, text="实时读取可选课程（S202 · 只读）",
            command=self.check_live_catalog,
        )
        self.live_catalog_button.pack(side="left", padx=(0, 8))
        self.live_poll_button = ttk.Button(
            timetable_row, text="开始自动刷新（≥60秒）",
            command=self.toggle_live_polling,
        )
        self.live_poll_button.pack(side="left")
        ttk.Label(
            auth_panel, textvariable=self.timetable_read_status,
            foreground="#666", wraplength=920,
        ).pack(anchor="w", pady=(4, 0))
        ttk.Label(
            auth_panel, textvariable=self.live_catalog_status,
            foreground="#416075", wraplength=920,
        ).pack(anchor="w", pady=(4, 0))

        monitor_panel = ttk.LabelFrame(
            self.tab_home, text="第 3 步  ·  课程余量监测（只读）",
            padding=(10, 6), style="KingoStep.TLabelframe",
        )
        monitor_panel.pack(fill="x", padx=10, pady=(0, 5))
        self.action_panel = ttk.Frame(monitor_panel)
        self.action_panel.pack(fill="x")
        self.start_button = ttk.Button(
            self.action_panel, text="开始查询课程余量", command=self.start_monitor)
        self.start_button.pack(side="left", padx=(0, 8))
        self.stop_button = ttk.Button(
            self.action_panel, text="停止查询", command=self.stop_monitor,
            state="disabled")
        self.stop_button.pack(side="left", padx=(0, 8))
        ttk.Button(
            self.action_panel, text="检查是否具备运行条件", command=self.preflight,
        ).pack(side="left")
        ttk.Label(
            monitor_panel,
            text="此功能只读学校数据，不会帮你提交选课；学校接口尚未核验时会拒绝启动。",
            foreground="#666",
        ).pack(anchor="w", pady=(6, 0))

        enrollment = ttk.Frame(self.tab_home, padding=(12, 3))
        enrollment.pack(fill="x")
        self.enrollment_gate_status = tk.StringVar(value=self.enrollment_gate_label())
        ttk.Label(
            enrollment, textvariable=self.enrollment_gate_status,
            foreground="#965b16",
        ).pack(side="left")
        self.scheduled_start_button = ttk.Button(
            enrollment, text="自动提交（暂不可用）",
            command=self.start_scheduled_enrollment, state="disabled",
        )
        self.scheduled_start_button.pack(side="right", padx=(8, 0))
        self.scheduled_stop_button = ttk.Button(
            enrollment, text="取消自动计划", command=self.stop_scheduled_enrollment,
            state="disabled",
        )
        self.scheduled_stop_button.pack(side="right")

        self.course_frame = ttk.LabelFrame(
            self.tab_home, text="目标课程列表 · 本地配置 / 只读状态",
            padding=8,
        )
        self.course_frame.pack(
            fill="both", expand=True, padx=10, pady=(3, 8))
        self.table = ttk.Treeview(
            self.course_frame,
            columns=("key", "course", "class_id", "teacher", "status"),
            show="headings",
        )
        for col, label, width in (
            ("key", "志愿", 70), ("course", "课程 / 代码", 225),
            ("class_id", "教学班", 110), ("teacher", "任课教师", 120),
            ("status", "查询结果", 240),
        ):
            self.table.heading(col, text=label)
            self.table.column(col, width=width, stretch=True)
        scroll = ttk.Scrollbar(
            self.course_frame, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=scroll.set)
        scroll.pack(side="right", fill="y")
        self.table.pack(fill="both", expand=True)
        self.table.bind(
            "<Double-1>", lambda _event: self.copy_selected_field("course_id"))
        ttk.Label(
            self.course_frame,
            text="双击课程复制代码。示例课程不是学校实时数据。",
            foreground="#666",
        ).pack(anchor="w", pady=(5, 0))

        # Page layouts remain independent; Application owns all state and callbacks.
        build_manual_page(self)
        build_tools_page(self)

    def toggle_advanced_panel(self) -> None:
        self.notebook.select(self.tab_tools)
        if self.advanced_panel.winfo_manager():
            self.advanced_panel.pack_forget()
        else:
            self.advanced_panel.pack(
                fill="x", before=self.log_frame, padx=10, pady=(0, 8))

    def toggle_portal_panel(self) -> None:
        """Navigate to manual backup instead of hiding the controls."""
        self.notebook.select(self.tab_manual)

    def show_function_guide(self) -> None:
        """Plain-language capability legend. No network or hidden actions."""
        messagebox.showinfo(
            "这些功能分别做什么？",
            "① 课程与志愿：编辑本地目标课程，已可使用，不会选课。\n\n"
            "② 软件内登录 / 查课：只有经过核验的登录协议和学期接口账号一致性"
            "检查都通过，才会在内存保存会话。当前华水动态登录协议仍待验收。\n\n"
            "③ 课程余量监测：只读取学校允许提供的课程信息，"
            "不会提交选课；需要真实接口核验后才能启动。\n\n"
            "自动提交：目前未接入华水真实选课接口，按钮保持禁用。\n\n"
            "手动官网备用：只会在浏览器打开华水官网，"
            "登录和选课必须由本人操作。\n\n"
            "工具与日志：本机诊断、模拟测试和高级配置，"
            "不代表学校接口已经接通。",
        )

    def enrollment_gate_label(self) -> str:
        """Show how many of the four write gates have valid evidence.

        This is a read-only indicator. It never enables the submit button: no
        NCWU write adapter is shipped, so 4/4 evidence still cannot submit.
        """
        registry = getattr(self, "attestations", None)
        if registry is None:
            return "自动选课：未接入提交适配器（验收证据 0/4）"
        try:
            report = registry.describe()
        except AttestationError:
            return "自动选课：未接入提交适配器（验收登记表无效）"
        return f"自动选课：未接入提交适配器（验收证据 {report['valid']}/{report['total']}）"

    def log(self, message: str) -> None:
        self.log_box.configure(state="normal")
        self.log_box.insert("end", time.strftime("%H:%M:%S") + "  " + message + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def edit_settings(self) -> None:
        if self.worker or self.__dict__.get("scheduled_worker"):
            messagebox.showinfo("请先停止监测", "运行期间无法修改配置，请先停止当前任务。")
            return
        try:
            SettingsEditor(self, Path(self.path.get()), self.check_config)
        except (ValueError, OSError, KeyError) as exc:
            self.log("配置编辑器无法启动：" + type(exc).__name__)
            messagebox.showerror("无法打开编辑器", "配置文件结构可能损坏，请先点击检查配置。")

    def manage_plans(self) -> None:
        if self.worker or self.__dict__.get("scheduled_worker"):
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
            text = self.log_box.get("1.0", "end-1c")
            body = text + self.latency_summary_text()
            Path(destination).write_text(body, encoding="utf-8")
        except OSError:
            messagebox.showerror("导出失败", "无法写入该文件，请检查目录权限。")

    def latency_summary_text(self) -> str:
        """Append phase percentiles so a failed run can be diagnosed offline."""
        report = summarize_file(app_dir() / DEFAULT_LATENCY_FILE)
        if not report.get("samples"):
            return "\n\n（暂无时延采样；本次运行没有完成任何课程读取。）\n"
        lines = ["", "", "时延采样（毫秒，本机统计，不含课程或账号信息）：",
                 f"  样本数：{report['samples']}"]
        for name, stats in report["phases"].items():
            lines.append(f"  {name}: p50={stats['p50']} p95={stats['p95']} "
                         f"max={stats['max']}")
        return "\n".join(lines) + "\n"

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
        if (self.worker or self.__dict__.get("scheduled_worker")
                or self._session_check_in_progress or self._closing
                or self.__dict__.get("_timetable_read_in_progress", False)
                or self.__dict__.get("_school_page_read_in_progress", False)
                or self.__dict__.get("_term_probe_in_progress", False)):
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
                result = asyncio.run(probe_own_session(
                    config, session_cookie=scoped_native_cookie(self, config)))
            except Exception:
                result = SessionProbeResult("unavailable", False)
            self.events.put(MonitorEvent("session_probe", result))

        try:
            threading.Thread(target=execute, name="KingoSessionProbe", daemon=True).start()
        except RuntimeError:
            self._session_check_in_progress = False
            self.session_check_button.configure(state="normal")
            self.status.set("会话核验线程无法启动；未请求学校")

    def start_native_login(self) -> None:
        """One credential form submission in the EXE, never browser automation."""
        if (self.worker or self.__dict__.get("scheduled_worker") or self._closing
                or self._native_login_in_progress or self._native_preparation_in_progress
                or self.__dict__.get("_live_catalog_in_progress", False)
                or self.__dict__.get("_s202_result_in_progress", False)
                or self.__dict__.get("_s202_round_probe_in_progress", False)
                or self.__dict__.get("_s202_score_in_progress", False)
                or self.__dict__.get("_course_plan_in_progress", False)
                or self._session_check_in_progress
                or self.__dict__.get("_timetable_read_in_progress", False)
                or self.__dict__.get("_school_page_read_in_progress", False)
                or self.__dict__.get("_term_probe_in_progress", False)):
            return
        try:
            config = read_config(Path(self.path.get()), offline=True)
        except (ValueError, TypeError, KeyError, OSError):
            self.native_login_status.set("本机配置无效，无法登录")
            return
        code = validate_ncwulogin_entry(config)
        if code is not None:
            self.native_login_status.set(NativeLoginResult(code).message)
            return
        username = simpledialog.askstring("EXE 内登录", "本人官方账号：", parent=self)
        if username is None:
            return
        password = simpledialog.askstring(
            "EXE 内登录", "官方密码（仅用于本次请求，不会保存）：",
            show="*", parent=self,
        )
        if password is None:
            return
        self._native_session_cookie = None
        self._native_session_origin = None
        self._native_login_in_progress = True
        self.native_login_button.configure(state="disabled")
        self.native_login_status.set("正在执行已核验的登录流程，并独立核对本人账号")

        def execute() -> None:
            try:
                result = asyncio.run(login_ncwusigned(config, username, password))
            except Exception:
                result = NativeLoginResult("unavailable")
            self.events.put(MonitorEvent("native_login", result))

        try:
            threading.Thread(target=execute, name="KingoNativeLogin", daemon=True).start()
        except RuntimeError:
            self._native_login_in_progress = False
            self.native_login_button.configure(state="normal")
            self.native_login_status.set("登录线程启动失败；没有保存会话")

    def clear_native_session(self) -> None:
        """Clear in-process credentials; this is not a verified server logout."""
        if self.__dict__.get("_live_polling", False):
            self.stop_live_polling()
        if (self.worker or self.__dict__.get("scheduled_worker")
                or self._native_login_in_progress
                or self._native_preparation_in_progress or self._session_check_in_progress
                or self.__dict__.get("_timetable_read_in_progress", False)
                or self.__dict__.get("_school_page_read_in_progress", False)
                or self.__dict__.get("_term_probe_in_progress", False)
                or self.__dict__.get("_s202_score_in_progress", False)
                or self.__dict__.get("_course_plan_in_progress", False)):
            return
        self._native_session_cookie = None
        self._native_session_origin = None
        self.native_login_status.set("已清除 EXE 内存会话；这不代表学校服务端注销")
        self.native_preparation_status.set("会话已清除，接口核验结果失效")

    def check_known_timetable(self) -> None:
        """Read the previously observed school timetable route, not electives."""
        if (self.worker or self.__dict__.get("scheduled_worker")
                or self._closing or self._native_login_in_progress
                or self._native_preparation_in_progress
                or self._session_check_in_progress
                or self._timetable_read_in_progress
                or self.__dict__.get("_school_page_read_in_progress", False)
                or self.__dict__.get("_term_probe_in_progress", False)):
            return
        try:
            config = read_config(Path(self.path.get()), offline=True)
        except (OSError, ValueError, TypeError, KeyError):
            self.timetable_read_status.set("本机配置无效；没有向学校发送请求")
            return
        # Snapshot the session on the Tk UI thread, never read mutable UI
        # state from the async network thread.
        _start_ncwu_read_worker(
            self, config=config, cookie=scoped_native_cookie(self, config),
            read_call=read_known_timetable,
            fallback_factory=TimetableReadResult,
            flag="_timetable_read_in_progress", button=self.timetable_read_button,
            status_var=self.timetable_read_status,
            running_message="正在核对既有课表接口与本人 EXE 会话",
            failure_message="课表读取线程启动失败；没有联网",
            event_kind="known_timetable", thread_name="KingoKnownTimetableRead",
        )

    def check_known_year_term(self) -> None:
        """One no-body POST to an observed school term API; never enrollment."""
        if (self.worker or self.__dict__.get("scheduled_worker")
                or self._closing or self._native_login_in_progress
                or self._native_preparation_in_progress
                or self._session_check_in_progress
                or self._timetable_read_in_progress
                or self._school_page_read_in_progress
                or self._term_probe_in_progress):
            return
        try:
            config = read_config(Path(self.path.get()), offline=True)
        except (OSError, ValueError, TypeError, KeyError):
            self.term_probe_status.set("配置无效，未向学校发送请求")
            return
        code = validate_term_contract(config)
        if code is not None:
            result = TermProbeResult(code)
            self.term_probe_status.set(result.message)
            self.status.set(result.message)
            return
        _start_ncwu_read_worker(
            self, config=config, cookie=scoped_native_cookie(self, config),
            read_call=probe_known_year_term,
            fallback_factory=TermProbeResult,
            flag="_term_probe_in_progress", button=self.term_probe_button,
            status_var=self.term_probe_status,
            running_message="正在通过 EXE 的独立会话核验学年学期接口",
            failure_message="线程启动失败；未访问学校",
            event_kind="known_year_term", thread_name="KingoKnownYearTerm",
        )

    def check_known_school_results(self) -> None:
        """Check the HAR-observed selected-course HTML page, never submit."""
        if (self.worker or self.__dict__.get("scheduled_worker")
                or self._closing or self._native_login_in_progress
                or self._native_preparation_in_progress
                or self._session_check_in_progress
                or self._timetable_read_in_progress
                or self._school_page_read_in_progress
                or self.__dict__.get("_term_probe_in_progress", False)):
            return
        try:
            config = read_config(Path(self.path.get()), offline=True)
        except (OSError, ValueError, TypeError, KeyError):
            self.school_page_status.set("本机配置无效；没有访问学校")
            return
        # Fail closed offline if a school query contract is incomplete.
        error = validate_known_page_contract(config, "selected_results")
        if error is not None:
            result = KnownSchoolPageResult(error, "selected_results")
            self.school_page_status.set(result.message)
            self.status.set(result.message)
            return
        _start_ncwu_read_worker(
            self, config=config, cookie=scoped_native_cookie(self, config),
            read_call=lambda cfg, *, session_cookie: read_known_school_page(
                cfg, "selected_results", session_cookie=session_cookie),
            fallback_factory=lambda code: KnownSchoolPageResult(code, "selected_results"),
            flag="_school_page_read_in_progress", button=self.school_page_button,
            status_var=self.school_page_status,
            running_message="正在核验本人已选课程结果网页（只读）",
            failure_message="读取线程启动失败；没有访问学校",
            event_kind="known_school_results", thread_name="KingoKnownSchoolResults",
        )

    def check_course_plan(self) -> None:
        """One snapshot of historical NCWU 5327042 course-plan rows."""
        if _ncwu_read_busy(self, include_polling=True):
            return
        if (not self._native_session_cookie
                or self._native_session_origin != "jwmis.ncwu.edu.cn"):
            self.course_plan_status.set("请先在 EXE 内登录本人教务账号")
            return
        try:
            config = read_config(Path(self.path.get()), offline=True)
        except (OSError, ValueError, TypeError, KeyError):
            self.course_plan_status.set("无法读取配置；未访问学校")
            return
        _start_ncwu_read_worker(
            self, config=config, cookie=scoped_native_cookie(self, config),
            read_call=read_current_course_plan, fallback_factory=PlanSnapshot,
            flag="_course_plan_in_progress", button=self.course_plan_button,
            status_var=self.course_plan_status,
            running_message="正在读取本学期开课计划快照；不查询正选实时余量",
            failure_message="开课计划读取线程未启动",
            event_kind="ncwu_course_plan", thread_name="KingoCoursePlanRead",
        )

    def show_course_plan_rows(self, result: PlanSnapshot) -> None:
        """One in-memory, explicitly historical plan snapshot popup."""
        popup = _open_readonly_popup(
            self, title="华水开课计划快照 · 非正选实时可用名额",
            size="1110x520", message=result.message, wraplength=1080,
        )
        # Compare the fresh plan to local course preferences without saving
        # anything or turning historical availability into a live seat claim.
        try:
            config = read_config(Path(self.path.get()), offline=True)
            alignment = compare_plan_targets(result, config)
            heading = alignment.message.splitlines()[0]
        except (OSError, ValueError, TypeError, KeyError):
            alignment = None
            heading = "尚未核对本地志愿：本地课程配置不可读取或格式不正确。"
        header = ttk.Frame(popup, padding=(12, 0, 12, 8))
        header.pack(fill="x")
        ttk.Label(
            header, text=heading, foreground="#41566c",
            wraplength=870,
        ).pack(side="left", fill="x", expand=True)
        if alignment is not None and alignment.targets:
            ttk.Button(
                header, text="查看逐项志愿核对",
                command=lambda report=alignment: messagebox.showinfo(
                    "开课计划 × 本地志愿（仅供准备）",
                    report.message, parent=popup),
            ).pack(side="right", padx=(8, 0))
        columns = ("name", "code", "class", "teacher", "cap", "enrolled",
                   "available", "time", "room")
        titles = ("课程", "课程代码", "教学班标识", "教师", "限选", "已选",
                  "计划可选", "上课时间", "上课地点")
        widths = (165, 120, 150, 115, 65, 65, 75, 150, 135)
        tree = _create_readonly_tree(
            popup, columns=columns, titles=titles, widths=widths,
            minwidth=55, height=17,
        )
        for row in result.rows:
            tree.insert("", "end", values=(
                row.name, row.code, row.class_id or "未知",
                row.teacher, row.capacity if row.capacity is not None else "?",
                row.enrolled if row.enrolled is not None else "?",
                row.plan_available if row.plan_available is not None else "?",
                row.time, row.room,
            ))
        scrollbar = ttk.Scrollbar(popup, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y", padx=(0, 10), pady=(0, 14))
        tree.pack(side="left", fill="both", expand=True, padx=(12, 0), pady=(0, 14))

    def copy_ncwu_contract_report(self) -> None:
        """Copy only locally generated structural evidence, never the HAR."""
        text = self.__dict__.get("_last_ncwu_contract_report")
        if not isinstance(text, str) or not text:
            messagebox.showinfo(
                "脱敏报告", "请先选择本人 HAR 完成本地接口核验。")
            return
        try:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.update_idletasks()
        except tk.TclError:
            messagebox.showwarning(
                "脱敏报告", "系统剪贴板暂不可用，未复制任何内容。")
            return
        self.log("已复制脱敏 HAR 接口字段报告；不含请求和响应的原始取值。")
        messagebox.showinfo("脱敏报告", "已复制字段结构报告。可用于继续核验接口，"
                             "不要附带原始 HAR 或账号凭证。")

    def inspect_ncwu_contracts(self) -> None:
        """Inspect only local HAR keys for two currently unverified routes."""
        filename = filedialog.askopenfilename(
            title="选择本人抓取的 HAR（仅本地解析，不会上传）",
            filetypes=[("HTTP Archive", "*.har *.json")],
        )
        if not filename:
            return
        try:
            path = Path(filename)
            if path.stat().st_size > 16 * 1024 * 1024:
                raise ValueError("file too large")
            report = inspect_two_endpoint_contracts(path.read_bytes())
        except (OSError, ValueError, TypeError):
            messagebox.showwarning(
                "接口证据核验", "无法读取文件或超过 16 MiB；没有访问学校。")
            return
        if report.code == "parsed":
            # Only this bounded, value-free report is retained in memory.
            # The raw HAR and all request/response bodies are discarded.
            self._last_ncwu_contract_report = report.message
        else:
            self._last_ncwu_contract_report = None
        self.log("已在本机检查 S202 初始化与资格预检 HAR 字段名；"
                 "没有保存、上传或发送捕获的请求与响应。")
        messagebox.showinfo("初始化／教学班预检契约（仅本机）",
                            report.message)

    def check_personal_s202_score(self) -> None:
        """Read own count using round fields from one current EXE session."""
        if _ncwu_read_busy(self):
            return
        if (not self._native_session_cookie
                or self._native_session_origin != "jwmis.ncwu.edu.cn"):
            self.s202_score_status.set("请先在 EXE 内登录本人教务账号")
            return
        try:
            config = read_config(Path(self.path.get()), offline=True)
        except (OSError, ValueError, TypeError, KeyError):
            self.s202_score_status.set("本地配置无效，没有访问学校")
            return
        _start_ncwu_read_worker(
            self, config=config, cookie=scoped_native_cookie(self, config),
            read_call=read_personal_s202_score, fallback_factory=PersonalScoreCount,
            flag="_s202_score_in_progress", button=self.s202_score_button,
            status_var=self.s202_score_status,
            running_message="正在读取本人当前 S202 已选门数（只读）",
            failure_message="查询线程启动失败；未访问学校",
            event_kind="ncwu_s202_score", thread_name="KingoS202ScoreRead",
        )

    def check_s202_round(self) -> None:
        """Fetch only official, non-sensitive S202 round/window metadata."""
        if _ncwu_read_busy(self):
            return
        if (self._native_session_cookie is None
                or self._native_session_origin != "jwmis.ncwu.edu.cn"):
            self.s202_round_status.set("请先在 EXE 内登录本人教务账号")
            return
        try:
            config = read_config(Path(self.path.get()), offline=True)
        except (OSError, ValueError, TypeError, KeyError):
            self.s202_round_status.set("配置无法读取，未查询学校")
            return
        _start_ncwu_read_worker(
            self, config=config, cookie=scoped_native_cookie(self, config),
            read_call=probe_s202_round, fallback_factory=S202RoundStatus,
            flag="_s202_round_probe_in_progress", button=self.s202_round_button,
            status_var=self.s202_round_status,
            running_message="正在读取学校官方 S202 轮次与时间窗（只读）",
            failure_message="轮次查询线程未启动；未访问学校",
            event_kind="ncwu_s202_round", thread_name="KingoS202RoundProbe",
        )

    def check_s202_selected_results(self) -> None:
        """Single native GET of current user's S202 result page, no submit."""
        if _ncwu_read_busy(self):
            return
        if (self._native_session_cookie is None
                or self._native_session_origin != "jwmis.ncwu.edu.cn"):
            self.s202_result_status.set("请先在 EXE 登录本人教务账号")
            return
        try:
            config = read_config(Path(self.path.get()), offline=True)
        except (OSError, ValueError, TypeError, KeyError):
            self.s202_result_status.set("配置读取失败，未访问学校")
            return
        _start_ncwu_read_worker(
            self, config=config, cookie=scoped_native_cookie(self, config),
            read_call=read_s202_selected_results, fallback_factory=S202ReadbackResult,
            flag="_s202_result_in_progress", button=self.s202_result_button,
            status_var=self.s202_result_status,
            running_message="正在通过 EXE 会话读取当前 S202 已选结果页（只读）",
            failure_message="只读结果读取线程未启动",
            event_kind="ncwu_s202_results", thread_name="KingoS202ResultReadback",
        )

    def show_s202_candidate_results(self, result: S202ReadbackResult) -> None:
        """Memory-only candidates; a table cannot certify enrollment success."""
        if not result.candidates:
            return
        popup = _open_readonly_popup(
            self, title="S202 已选结果候选记录 · 待官网核对",
            size="760x400", message=result.message, wraplength=720,
        )
        columns = ("name", "code", "class_number", "teacher")
        titles = ("课程名", "课程代码", "教学班号", "教师")
        table = _create_readonly_tree(
            popup, columns=columns, titles=titles,
            widths=(180,) * len(columns), minwidth=110,
        )
        for item in result.candidates:
            table.insert("", "end", values=(
                item.course_name, item.course_code,
                item.class_number, item.teacher))
        table.pack(fill="both", expand=True, padx=12, pady=(0, 14))

    def check_live_catalog(self) -> None:
        """Read the current S202 form/window/catalog via the EXE's own session."""
        # Unlike the course-plan snapshot, manual catalog refresh is also
        # used by the existing opt-in >=60s polling workflow.
        if _ncwu_read_busy(self):
            return
        if not self._native_session_cookie or self._native_session_origin != "jwmis.ncwu.edu.cn":
            self.live_catalog_status.set("请先点击「在 EXE 登录本人教务账号」")
            return
        try:
            config = read_config(Path(self.path.get()), offline=True)
        except (ValueError, TypeError, KeyError, OSError):
            self.live_catalog_status.set("配置读取失败；未查询学校")
            return
        _start_ncwu_read_worker(
            self, config=config, cookie=scoped_native_cookie(self, config),
            read_call=live_s202_snapshot, fallback_factory=LiveElectiveSnapshot,
            flag="_live_catalog_in_progress", button=self.live_catalog_button,
            status_var=self.live_catalog_status,
            running_message="正在从官网读取当前正选表单、时间窗及课程列表（只读）",
            failure_message="查询线程无法启动；未访问学校",
            event_kind="ncwu_live_catalog", thread_name="KingoLiveS202Query",
        )

    def stop_live_polling(self) -> None:
        self._live_polling = False
        timer = self._live_poll_timer
        self._live_poll_timer = None
        if timer is not None:
            self.after_cancel(timer)
        self.live_poll_button.configure(text="开始自动刷新（≥60秒）")

    def toggle_live_polling(self) -> None:
        """Opt-in, budget-gated polling. Never enabled by default."""
        if self._live_polling:
            self.stop_live_polling()
            self.live_catalog_status.set("已停止自动查询；当前已完成的手动结果仍可查看")
            return
        try:
            config = read_config(Path(self.path.get()), offline=True)
            policy = config.get("server_policy", {})
        except (ValueError, TypeError, KeyError, OSError):
            self.live_catalog_status.set("请求预算配置不可读取，未开始自动查询")
            return
        if (not isinstance(policy, dict) or policy.get("verified") is not True
                or not policy.get("windows")):
            self.live_catalog_status.set(
                "自动刷新需要已经核验的学校请求频率规则；仍可手动单次查课")
            return
        if not self._native_session_cookie or self._native_session_origin != "jwmis.ncwu.edu.cn":
            self.live_catalog_status.set("请先在 EXE 登录本人账号")
            return
        self._live_polling = True
        self.live_poll_button.configure(text="停止自动刷新")
        self.check_live_catalog()
        if not self._live_catalog_in_progress:
            self.stop_live_polling()

    def show_live_catalog_rows(self, result: LiveElectiveSnapshot) -> None:
        """Reuse a single memory-only course table on repeated snapshots."""
        popup = self._live_catalog_popup
        if popup is None or not popup.winfo_exists():
            popup = tk.Toplevel(self)
            popup.title("华水 S202 · 实时课程目录（非已选结果）")
            popup.geometry("1160x530")
            popup.transient(self)
            self._live_catalog_popup = popup
            self._live_catalog_message = tk.StringVar(value="")
            ttk.Label(
                popup, textvariable=self._live_catalog_message, wraplength=1120,
            ).pack(anchor="w", padx=12, pady=(12, 6))
            target_header = ttk.Frame(popup, padding=(12, 0, 12, 8))
            target_header.pack(fill="x")
            self._live_target_message = tk.StringVar(
                value="尚未与本地志愿关联；仅供课程目录核对")
            ttk.Label(
                target_header, textvariable=self._live_target_message,
                wraplength=955, foreground="#41566c",
            ).pack(side="left", fill="x", expand=True)
            self._live_target_review_button = ttk.Button(
                target_header, text="查看志愿候选分析",
                command=self.show_live_target_review, state="disabled",
            )
            self._live_target_review_button.pack(side="right", padx=(8, 0))
            columns = ("name", "code", "class_number", "teacher", "selection_status", "points", "target")
            titles = ("课程名称", "课程代码", "显示班号", "教师", "选课状态", "选课币字段", "本地志愿（仅候选）")
            widths = (210, 145, 135, 115, 145, 110, 160)
            table = _create_readonly_tree(
                popup, columns=columns, titles=titles, widths=widths,
                minwidth=80, height=15,
            )
            scrollbar = ttk.Scrollbar(popup, orient="vertical", command=table.yview)
            table.configure(yscrollcommand=scrollbar.set)
            scrollbar.pack(side="right", fill="y", padx=(0, 10), pady=(0, 14))
            table.pack(side="left", fill="both", expand=True, padx=(12, 0), pady=(0, 14))
            self._live_catalog_tree = table
        self._live_catalog_message.set(result.message)
        try:
            config = read_config(Path(self.path.get()), offline=True)
            report = compare_live_targets(result, config)
            self._live_target_report = report
            self._live_target_message.set(report.message.splitlines()[0])
            self._live_target_review_button.configure(
                state="normal" if report.targets else "disabled")
        except (ValueError, TypeError, KeyError, OSError):
            report = None
            self._live_target_report = None
            self._live_target_message.set(
                "本地志愿配置无法读取或验证，仍可查看学校实时课程列表。")
            self._live_target_review_button.configure(state="disabled")
        table = self._live_catalog_tree
        for iid in table.get_children():
            table.delete(iid)
        for index, course in enumerate(result.courses):
            marks = (
                "、".join(report.row_marks[index])
                if report is not None and index < len(report.row_marks)
                else ""
            )
            table.insert("", "end", values=(
                course.course_name, course.course_code, course.class_number,
                course.teacher, course.selection_status, course.points_label,
                ("课程候选：" + marks) if marks else "",
            ))

    def show_live_target_review(self) -> None:
        """Explain candidate courses without equating skbh with skbjdm."""
        report = self.__dict__.get("_live_target_report")
        if report is None or not report.targets:
            messagebox.showinfo(
                "志愿候选分析", "当前没有可核对的本地目标课程。")
            return
        messagebox.showinfo(
            "S202 正选列表 × 本地志愿（只读分析）",
            report.message, parent=self._live_catalog_popup,
        )

    def check_native_preparation(self) -> None:
        """Explicit in-EXE identity + catalog HTTP checks, never browser automation."""
        if (self.worker or self.__dict__.get("scheduled_worker")
                or self._session_check_in_progress
                or self.__dict__.get("_live_catalog_in_progress", False)
                or self.__dict__.get("_native_login_in_progress", False)
                or self._native_preparation_in_progress or self._closing
                or self.__dict__.get("_timetable_read_in_progress", False)
                or self.__dict__.get("_school_page_read_in_progress", False)
                or self.__dict__.get("_term_probe_in_progress", False)):
            return
        try:
            config = read_config(Path(self.path.get()), offline=True)
        except (ValueError, TypeError, KeyError, OSError):
            messagebox.showwarning("接口准备失败", "本机配置无效，请先修正。")
            return
        self._native_preparation_in_progress = True
        self.native_prepare_button.configure(state="disabled")
        self.native_preparation_status.set("EXE 正在核验会话和课程目录（只读）")

        def execute() -> None:
            try:
                result = asyncio.run(prepare_native_api(
                    config, session_cookie=scoped_native_cookie(self, config)))
            except Exception:
                result = NativePreparationResult("unavailable")
            self.events.put(MonitorEvent("native_preparation", result))

        try:
            threading.Thread(target=execute, name="KingoNativeApiPreparation",
                             daemon=True).start()
        except RuntimeError:
            self._native_preparation_in_progress = False
            self.native_prepare_button.configure(state="normal")
            self.native_preparation_status.set("接口准备线程启动失败；未访问学校")

    def inspect_scheduled_runner(self) -> None:
        """Validate the real runner's local admission contract, never start it."""
        if self.worker or self.__dict__.get("scheduled_worker"):
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
                or self.__dict__.get("_session_check_in_progress", False)):
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
        if self.__dict__.get("_official_portal_appointment") is not None:
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
        if "session_check_button" in self.__dict__:
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
        if self.__dict__.get("scheduled_worker") is not None:
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
        if self.__dict__.get("scheduled_worker") is not None:
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
        if self.worker or self.__dict__.get("scheduled_worker"):
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
        if self.worker or self.__dict__.get("scheduled_worker"):
            return
        try:
            config = read_config(Path(self.path.get()), offline=True)
            config = resolve_state_file(config, app_dir())
            cookie = scoped_native_cookie(self, config)
            checks = (inspect_readiness(config, session_cookie=cookie)
                      if cookie is not None else inspect_readiness(config))
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
            placeholder = str(item.get("course_id") or "") in (
                "FILL_FROM_OFFICIAL_PAGE", "SECOND_OPTION_ID")
            label = "仅示例 · 未连接" if placeholder else "未查询"
            self.table.insert("", "end", iid=key, values=(key, course, class_id, teacher, label))

    def demo_next(self) -> None:
        """Step through 3 bundled fictional snapshots; never touch the school."""
        if self.worker or self.__dict__.get("scheduled_worker"):
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
        if self.worker or self.__dict__.get("scheduled_worker") or self._entry_probe_in_progress:
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
        if self.worker or self.__dict__.get("scheduled_worker"):
            messagebox.showinfo("请先停止监测", "监测运行期间请勿切换离线分析。")
            return
        filename = filedialog.askopenfilename(title="选择本人已授权导出的 HAR（仅本地分析）",
                                              filetypes=[("HAR 文件", "*.har")])
        if not filename:
            return
        try:
            summary = summarize_har(Path(filename))
            messagebox.showinfo("HAR 结构与 JSON 线索（不代表接口已验证）", format_summary(summary))
            self.log(f"本机 HAR 结构与只读 JSON 线索分析完成：包含 {summary.matching_school_origin} 条华水 HTTPS 请求；未连接学校。")
        except (OSError, ValueError):
            messagebox.showerror("HAR 检查失败", "文件结构无效、文件过大或无法读取；请勿上传未经脱敏的 HAR。")

    def audit_auth_route_har(self) -> None:
        """Classify CAS and school redirect evidence from local HAR only."""
        if self.worker or self.__dict__.get("scheduled_worker"):
            messagebox.showinfo("请先停止监测", "认证链路离线分析不能与后台任务同时进行。")
            return
        filename = filedialog.askopenfilename(
            title="选择本机本人授权导出的 HAR（不上传，不重放）",
            filetypes=[("HAR 文件", "*.har")],
        )
        if not filename:
            return
        try:
            summary = summarize_auth_route_har(Path(filename))
            messagebox.showinfo("华水认证链路（仅离线结构）", summary.message)
            self.log(
                f"本机身份认证链路分析完成：{summary.observed_route}；"
                "仅统计，无学校请求，未启用 CAS 登录或选课提交。"
            )
        except (ValueError, OSError):
            messagebox.showerror(
                "认证链路分析失败",
                "HAR 无效、过大或无法读取。请勿上传包含登录凭证的原始 HAR。"
            )

    def inspect_elective_table_file(self) -> None:
        """Inspect a locally saved S202 table only; never access campus services."""
        if self.worker or self.__dict__.get("scheduled_worker"):
            messagebox.showinfo("请先停止监测", "监测期间请勿切换离线分析。")
            return
        filename = filedialog.askopenfilename(
            title="选择本人导出的选课表 HTML（完全离线）",
            filetypes=[("HTML 文件", "*.html *.htm")],
        )
        if not filename:
            return
        try:
            page = Path(filename)
            if page.stat().st_size > 2 * 1024 * 1024:
                raise ValueError("oversized HTML")
            outcome = inspect_elective_table(page.read_bytes())
        except (OSError, ValueError):
            messagebox.showwarning(
                "选课表头核验", "文件无法读取或超过 2 MiB；未访问学校。")
            return
        self.status.set("选课表头核验（离线）｜" + outcome.code)
        self.log(
            f"已识别 {outcome.recognized_headers} 个选课表字段；"
            f"疑似数据行 {outcome.candidate_data_rows}；未联网。")
        messagebox.showinfo(
            "选课表头核验（仅离线）",
            outcome.message + "\n\n此操作不能确认剩余名额或开放选课，"
            "也不会启用自动提交。",
        )

    def review_catalog_evidence(self) -> None:
        """Compare a locally redacted evidence pair; never enable live catalog mode."""
        if self.worker or self.__dict__.get("scheduled_worker"):
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
        if self.worker or self.__dict__.get("scheduled_worker") or self._benchmark_in_progress:
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
        if self.worker or self.__dict__.get("scheduled_worker"):
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
        if (self.worker or self.__dict__.get("scheduled_worker")
                or self._benchmark_in_progress
                or self.__dict__.get("_timetable_read_in_progress", False)):
            return
        try:
            # First run offline checks so placeholders and missing authorization
            # are explained before any attempt at a network connection.
            config = read_config(Path(self.path.get()), offline=True)
            # One exact snapshot for preflight and the running client. Avoid
            # a second YAML read between authorization and worker construction.
            config = resolve_state_file(config, app_dir())
            session_cookie = scoped_native_cookie(self, config)
            checks = (inspect_readiness(config, session_cookie=session_cookie)
                      if session_cookie is not None else inspect_readiness(config))
            blockers, warnings = split_checks(checks)
            if blockers:
                self.status.set(f"无法启动｜{len(blockers)} 项本地前提未完成")
                messagebox.showwarning("先完成启动预检", format_checks(checks))
                self.log(f"启动被本地预检拦截：{len(blockers)} 项；没有连接学校。")
                return
            if session_cookie is not None:
                validate(config, check_only=False, session_cookie=session_cookie)
            else:
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
        session_cookie = scoped_native_cookie(self, config)
        self.worker = (MonitorWorker(config, self.events, session_cookie=session_cookie)
                       if session_cookie is not None else MonitorWorker(config, self.events))
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
                    if not self.worker and not self.__dict__.get("scheduled_worker"):
                        return
                elif event.kind == "tray_error":
                    self.restore_window()
                    self.log("系统托盘不可用，主窗口已恢复；任务未被静默终止。")
                elif event.kind == "status":
                    apply_monitor_status(
                        self, event.value, change_detector=status_changes)
                elif event.kind == "official_probe_done":
                    self._entry_probe_in_progress = False
                    self.status.set("官网登录页检测完毕｜未验证课程查询接口")
                    self.log("本次官网登录页检测结束；不代表本人已登录或课程余量可读取。")
                    messagebox.showinfo(
                        "官方登录页可达性（非选课接口）",
                        format_official_probe_results(
                            event.value, entries=OFFICIAL_ENTRIES),
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
                elif event.kind == "native_login":
                    self._native_login_in_progress = False
                    self.native_login_button.configure(state="normal")
                    result = event.value
                    self._native_session_cookie = result.session_cookie if result.verified else None
                    self._native_session_origin = result.session_origin if result.verified else None
                    self.native_login_status.set(result.message)
                    self.native_preparation_status.set(
                        "EXE 会话已通过账号核验；课程目录和余量需另行验证" if result.verified
                        else "账号未核验通过；不会使用当前会话读取课程"
                    )
                    self.status.set(result.message)
                    self.log(result.message)
                elif event.kind == "ncwu_live_catalog":
                    result = event.value
                    _finish_ncwu_read_event(
                        self, result, flag="_live_catalog_in_progress",
                        button=self.live_catalog_button,
                        status_var=self.live_catalog_status,
                        log_message=(
                            f"S202 实时查询：{result.code}，窗口={result.window}，"
                            f"记录={len(result.courses)}，阶段={result.stage}；未提交选课。"
                        ),
                        expired_message="官网表示会话失效，请重新登录",
                    )
                    if result.code == "catalog_read":
                        self.show_live_catalog_rows(result)
                    if self._live_polling:
                        if result.code == "catalog_read" and not self._closing:
                            # >=60s local spacing; a stricter verified shared
                            # request budget still governs every next HTTP call.
                            self._live_poll_timer = self.after(
                                60_000, self.check_live_catalog)
                        else:
                            self.stop_live_polling()
                elif event.kind == "known_timetable":
                    self._timetable_read_in_progress = False
                    self.timetable_read_button.configure(state="normal")
                    result = event.value
                    self.timetable_read_status.set(result.message)
                    self.status.set(result.message)
                    self.log(result.message)
                    if result.code == "expired":
                        self._native_session_cookie = None
                        self._native_session_origin = None
                        self.native_login_status.set("课表接口提示登录失效，请重新授权")
                elif event.kind == "known_year_term":
                    self._term_probe_in_progress = False
                    self.term_probe_button.configure(state="normal")
                    result = event.value
                    self.term_probe_status.set(result.message)
                    self.status.set(result.message)
                    self.log(result.message)
                    if result.code == "expired":
                        self._native_session_cookie = None
                        self._native_session_origin = None
                        self.native_login_status.set("学期接口提示会话过期，请重新授权")
                elif event.kind == "ncwu_course_plan":
                    result = event.value
                    _finish_ncwu_read_event(
                        self, result, flag="_course_plan_in_progress",
                        button=self.course_plan_button,
                        status_var=self.course_plan_status,
                        log_message=(
                            f"开课计划快照：{result.code}，"
                            f"教学班={len(result.rows)}，阶段={result.stage}；"
                            "不是正选实时余量，未提交选课。"
                        ),
                        expired_message="开课计划接口提示会话失效，请重新登录",
                    )
                    if result.code == "read":
                        self.show_course_plan_rows(result)
                elif event.kind == "ncwu_s202_score":
                    result = event.value
                    _finish_ncwu_read_event(
                        self, result, flag="_s202_score_in_progress",
                        button=self.s202_score_button,
                        status_var=self.s202_score_status,
                        log_message=(
                            f"S202 本人汇总查询：{result.code}，"
                            f"确认已选门数={result.selected_count is not None}；未提交选课。"
                        ),
                        expired_message="汇总接口提示登录已失效，请重新登录",
                    )
                elif event.kind == "ncwu_s202_round":
                    result = event.value
                    _finish_ncwu_read_event(
                        self, result, flag="_s202_round_probe_in_progress",
                        button=self.s202_round_button,
                        status_var=self.s202_round_status,
                        log_message=(
                            f"S202 轮次核验：{result.code}，窗口={result.window}，"
                            f"轮次字段={result.field_count}/6；未提交选课。"
                        ),
                        expired_message="S202 轮次接口提示登录失效，请重新登录",
                    )
                elif event.kind == "ncwu_s202_results":
                    result = event.value
                    _finish_ncwu_read_event(
                        self, result, flag="_s202_result_in_progress",
                        button=self.s202_result_button,
                        status_var=self.s202_result_status,
                        log_message=(
                            f"S202 已选结果只读检查：{result.code}，"
                            f"候选={len(result.candidates)}；未认定选课成功。"
                        ),
                        expired_message="S202 页面表示登录已失效，请重新登录",
                    )
                    if result.code == "page_observed":
                        self.show_s202_candidate_results(result)
                elif event.kind == "known_school_results":
                    self._school_page_read_in_progress = False
                    self.school_page_button.configure(state="normal")
                    result = event.value
                    self.school_page_status.set(result.message)
                    self.status.set(result.message)
                    self.log(result.message)
                    if result.code == "expired":
                        self._native_session_cookie = None
                        self._native_session_origin = None
                        self.native_login_status.set("学校页面提示登录失效，请重新授权")
                elif event.kind == "native_preparation":
                    self._native_preparation_in_progress = False
                    self.native_prepare_button.configure(state="normal")
                    result = event.value
                    self.native_preparation_status.set(result.message)
                    if result.code == "expired":
                        self._native_session_cookie = None
                        self._native_session_origin = None
                        self.native_login_status.set("应用登录会话已过期，请重新登录")
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
                        label = scheduled_reason_text(outcome.reason)
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
                    if ("session_check_button" in self.__dict__
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
                    if self._closing and not self.__dict__.get("scheduled_worker"):
                        self.destroy()
                        return
        except queue.Empty:
            pass
        if not self._closing or self.worker or self.__dict__.get("scheduled_worker"):
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
        self._native_session_cookie = None
        self._native_session_origin = None
        self._official_portal_appointment = None
        self._portal_prelogin_opened = False
        self._tray.stop()
        if self.worker:
            self.worker.stop()
        scheduled = self.__dict__.get("scheduled_worker")
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
                if app.native_login_button.winfo_manager() != "pack":
                    raise AssertionError("In-EXE login button missing from primary Windows UI")
                if app.native_prepare_button.winfo_manager() != "pack":
                    raise AssertionError("Native identity/catalog API check not mounted")
                if app.timetable_read_button.winfo_manager() != "pack":
                    raise AssertionError("Previously captured native timetable action missing")
                if "既有接口" not in str(app.timetable_read_button.cget("text")):
                    raise AssertionError("Known timetable API must be distinguished from elective catalog")
                if app.school_page_button.winfo_manager() != "pack":
                    raise AssertionError("Observed school results readback must be mounted under tools")
                if app.term_probe_button.winfo_manager() != "pack":
                    raise AssertionError("Observed no-body YearTerm EXE probe missing")
                if app.school_page_button.winfo_ismapped():
                    raise AssertionError("Advanced readback action must stay off the home tab")
                with patch("core.known_school_pages.httpx.AsyncClient",
                           side_effect=AssertionError("unverified result-page query attempted network")):
                    app.check_known_school_results()
                if "旧 HAR" not in app.school_page_status.get():
                    raise AssertionError("Unverified school query must stop with an explicit message")
                with patch("core.ncwu_term_probe.httpx.AsyncClient",
                           side_effect=AssertionError("unverified term probe attempted network")):
                    app.check_known_year_term()
                if "旧 HAR" not in app.term_probe_status.get():
                    raise AssertionError("Unverified term probe must stop without network")
                # Clicking native login can now collect credentials, but a
                # cancelled account prompt must never issue network requests.
                with patch.object(simpledialog, "askstring", return_value=None) as prompt, \
                     patch("core.ncwu_signed_login.httpx.AsyncClient",
                           side_effect=AssertionError("cancelled login attempted network")):
                    app.start_native_login()
                    prompt.assert_called_once()
                if app._native_session_cookie is not None:
                    raise AssertionError("Unverified login must not produce a session")
                if app.notebook.index("current") != 0:
                    raise AssertionError("The first tab must be the selection homepage")
                if app.portal_panel.winfo_manager() != "pack":
                    raise AssertionError("Manual backup must exist in its own tab")
                if app.portal_panel.winfo_ismapped():
                    raise AssertionError("Manual backup must not be visible on home")
                if app.log_box.winfo_ismapped():
                    raise AssertionError("Developer logs must not crowd the homepage")
                if app.status_bar.winfo_manager() != "pack":
                    raise AssertionError("The live status footer must remain visible")
                app.toggle_portal_panel()
                if app.notebook.select() != str(app.tab_manual):
                    raise AssertionError("Manual menu should navigate to backup tab")
                app.notebook.select(app.tab_home)
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
