"""Independent KingoAssistant Tk page layouts.

Only creates widgets and binds existing Application callbacks and StringVar
instances. The Application owns every control attribute, the event queue,
session state, configuration and the background worker lifecycle.

Layout language: dense Windows desktop groups with one command row per
concern, a wide data grid in the middle and the submit gate at the bottom.
Text is deliberately short so a 1024px-wide window shows the whole workflow
without horizontal scrolling.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from core.official_entries import OFFICIAL_ENTRIES, EntryKind
from windows_visual_shell import FONT_MONO, PALETTE

BUTTON_GAP = (0, 5)

ENTRY_KIND_LABELS = {
    EntryKind.WEB_DIRECT: "网页直连",
    EntryKind.SSO_BROWSER: "统一身份认证",
    EntryKind.MOBILE_APP: "手机 App",
}
ROW_GAP = (0, 3)


def _group(parent, title: str, *, padding=(8, 4)):
    """One WinForms-style group box; the caller fills it with dense rows."""
    return ttk.LabelFrame(
        parent, text=" " + title + " ", padding=padding, style="TLabelframe")


def _row(parent) -> ttk.Frame:
    row = ttk.Frame(parent)
    row.pack(fill="x", pady=ROW_GAP)
    return row


def _button(parent, text, command, *, style=None, side="left", **pack):
    options = {"text": text, "command": command}
    if style:
        options["style"] = style
    button = ttk.Button(parent, **options)
    button.pack(
        side=side, padx=BUTTON_GAP if side == "left" else (5, 0), **pack)
    return button


def _status(parent, variable, *, wraplength=980, hint=False):
    return ttk.Label(
        parent, textvariable=variable,
        style="Hint.TLabel" if hint else "Muted.TLabel",
        wraplength=wraplength, justify="left",
    )


def build_home_page(app) -> None:
    """Home = login → local targets → read-only stock query → submit gate.

    Fixed dense layout: three command groups and a grid on top, the submit
    gate pinned to the bottom. The grid absorbs whatever height is left, so
    the primary action is never pushed below the fold.
    """
    root = app.tab_home

    # ---- 5. submit gate (packed first so it always keeps its space) --
    band = ttk.Frame(root)
    band.pack(side="bottom", fill="x", padx=6, pady=(0, 6))
    gate = _group(band, "⑤ 自动提交与验收证据")
    gate.pack(fill="x")
    gate_row = ttk.Frame(gate)
    gate_row.pack(fill="x")
    app.scheduled_start_button = ttk.Button(
        gate_row, text="自动提交（暂不可用）",
        command=app.start_scheduled_enrollment, state="disabled")
    app.scheduled_start_button.pack(side="left", padx=BUTTON_GAP)
    app.scheduled_stop_button = ttk.Button(
        gate_row, text="取消自动计划",
        command=app.stop_scheduled_enrollment, state="disabled")
    app.scheduled_stop_button.pack(side="left", padx=BUTTON_GAP)
    ttk.Label(
        gate_row, text="写能力由学校接口验收决定，本机不会发出选课请求。",
        style="Muted.TLabel").pack(side="left", padx=(8, 0))
    app.enrollment_gate_status = tk.StringVar(value=app.enrollment_gate_label())
    _status(gate, app.enrollment_gate_status, hint=True, wraplength=1080).pack(
        anchor="w", pady=(3, 0))

    # ---- 1. account -------------------------------------------------
    auth = _group(root, "① 登录与账号（协议已接入 · 待实机验收）")
    auth.pack(fill="x", padx=6, pady=(4, 3))
    auth_row = ttk.Frame(auth)
    auth_row.pack(fill="x")
    app.native_login_button = _button(
        auth_row, "登录本人教务账号", app.start_native_login)
    app.native_prepare_button = _button(
        auth_row, "检查登录与课程数据（只读）", app.check_native_preparation)
    app.native_clear_button = _button(
        auth_row, "清除登录状态", app.clear_native_session)
    ttk.Label(
        auth_row, text="会话只保存在本进程内存，关闭窗口即失效。",
        style="Muted.TLabel").pack(side="left", padx=(10, 0))
    _status(auth, app.native_login_status).pack(anchor="w", pady=(2, 0))
    _status(auth, app.native_preparation_status).pack(anchor="w")

    # ---- 2. local targets -------------------------------------------
    setup = _group(root, "② 目标课程与方案（只写本机配置）")
    setup.pack(fill="x", padx=6, pady=ROW_GAP)
    setup_row = ttk.Frame(setup)
    setup_row.pack(fill="x")
    app.edit_button = _button(setup_row, "填写 / 修改目标课程", app.edit_settings)
    app.plans_button = _button(setup_row, "管理课程方案", app.manage_plans)
    ttk.Label(
        setup_row, text="本地计划不会向学校提交选课；保存后回到本页即可查询。",
        style="Muted.TLabel").pack(side="left", padx=(10, 0))
    path_row = ttk.Frame(setup)
    path_row.pack(fill="x", pady=(2, 0))
    ttk.Label(path_row, text="配置文件", style="Muted.TLabel").pack(side="left")
    ttk.Label(
        path_row, textvariable=app.path, style="Muted.TLabel",
        anchor="w").pack(side="left", fill="x", expand=True, padx=(6, 0))

    # ---- 3. read-only stock query + the single call to action --------
    monitor = _group(root, "③ 课程余量查询（只读 · 不提交）")
    monitor.pack(fill="x", padx=6, pady=ROW_GAP)
    app.action_panel = ttk.Frame(monitor)
    app.action_panel.pack(fill="x")
    app.start_button = ttk.Button(
        app.action_panel, text="开始查询余量", command=app.start_monitor,
        style="KingoPrimary.TButton")
    app.start_button.pack(side="right", padx=(10, 0))
    actions = ttk.Frame(app.action_panel)
    actions.pack(side="left", fill="x", expand=True)
    action_row = ttk.Frame(actions)
    action_row.pack(fill="x")
    app.stop_button = _button(action_row, "停止查询", app.stop_monitor)
    app.stop_button.configure(state="disabled")
    _button(action_row, "检查是否具备运行条件", app.preflight)
    tk.Frame(action_row, bg=PALETTE["stroke_soft"], width=1).pack(
        side="left", fill="y", padx=6, pady=2)
    app.timetable_read_button = _button(
        action_row, "读取本人课表（既有接口 · 只读）", app.check_known_timetable)
    app.live_catalog_button = _button(
        action_row, "实时读取可选课程（S202 · 只读）", app.check_live_catalog)
    app.live_poll_button = _button(
        action_row, "开始自动刷新（≥60秒）", app.toggle_live_polling)
    _status(actions, app.timetable_read_status).pack(anchor="w")
    _status(actions, app.live_catalog_status).pack(anchor="w")

    # ---- 4. data grid (absorbs the remaining height) -----------------
    app.course_frame = _group(root, "④ 目标课程列表 · 本地配置 / 只读状态")
    app.course_frame.pack(fill="both", expand=True, padx=6, pady=ROW_GAP)
    grid_wrap = ttk.Frame(app.course_frame)
    grid_wrap.pack(fill="both", expand=True)
    app.table = ttk.Treeview(
        grid_wrap,
        columns=("key", "course", "class_id", "teacher", "status"),
        show="headings", height=6, selectmode="browse",
    )
    for column, heading, width, stretch in (
        ("key", "志愿", 62, False),
        ("course", "课程 / 代码", 240, True),
        ("class_id", "教学班", 110, False),
        ("teacher", "任课教师", 110, False),
        ("status", "查询结果", 320, True),
    ):
        app.table.heading(column, text=heading, anchor="w")
        app.table.column(column, width=width, stretch=stretch, anchor="w")
    table_bar = ttk.Scrollbar(
        grid_wrap, orient="vertical", command=app.table.yview)
    app.table.configure(yscrollcommand=table_bar.set)
    table_bar.pack(side="right", fill="y")
    app.table.pack(side="left", fill="both", expand=True)
    app.table.bind(
        "<Double-1>", lambda _event: app.copy_selected_field("course_id"))
    ttk.Label(
        app.course_frame,
        text="双击某一行复制课程代码。示例课程不是学校实时数据。",
        style="Muted.TLabel").pack(anchor="w", pady=(3, 0))


def build_manual_page(app) -> None:
    """Manual official-site fallback, deliberately separate from the worker."""
    root = app.tab_manual
    ttk.Label(
        root,
        text="手动备用：只打开华水官方网页，登录、选课与提交均由本人完成。",
        style="Hint.TLabel", wraplength=1000,
    ).pack(anchor="w", padx=8, pady=(8, 4))

    # ---- manual actions ---------------------------------------------
    actions_group = _group(root, "手动操作（只调用系统浏览器）")
    actions_group.pack(fill="x", padx=8, pady=(0, 3))
    entry_row = ttk.Frame(actions_group)
    entry_row.pack(fill="x")
    _button(entry_row, "现在打开华水官网", app.open_official_portal)
    _button(entry_row, "复制所选课程代码并打开官网", app.prepare_selected_course)
    app.session_check_button = _button(
        entry_row, "检查软件登录状态（只读）", app.check_script_session)
    ttk.Label(
        actions_group,
        text="浏览器会话与软件内会话互不相通，这里不会代你登录。",
        style="Muted.TLabel").pack(anchor="w", pady=(3, 0))

    # ---- static entry inventory -------------------------------------
    entries_group = _group(root, "华水官方入口清单（静态内置 · 无联网）")
    entries_group.pack(fill="both", expand=True, padx=8, pady=(0, 3))
    grid_wrap = ttk.Frame(entries_group)
    grid_wrap.pack(fill="both", expand=True)
    app.portal_entries_table = ttk.Treeview(
        grid_wrap, columns=("name", "address", "kind"),
        show="headings", height=4, selectmode="browse",
    )
    for column, heading, width, stretch in (
        ("name", "入口", 180, False),
        ("address", "地址", 470, True),
        ("kind", "启动方式", 110, False),
    ):
        app.portal_entries_table.heading(column, text=heading, anchor="w")
        app.portal_entries_table.column(
            column, width=width, stretch=stretch, anchor="w")
    entries_bar = ttk.Scrollbar(
        grid_wrap, orient="vertical", command=app.portal_entries_table.yview)
    app.portal_entries_table.configure(yscrollcommand=entries_bar.set)
    entries_bar.pack(side="right", fill="y")
    app.portal_entries_table.pack(side="left", fill="both", expand=True)
    for entry in OFFICIAL_ENTRIES:
        app.portal_entries_table.insert("", "end", iid=entry.identifier, values=(
            entry.name,
            entry.launch_url or "在手机上使用官方喜鹊儿 App",
            ENTRY_KIND_LABELS[entry.kind],
        ))
    ttk.Label(
        entries_group,
        text="登录入口不等于选课接口；清单只作为手动操作的跳转地址。",
        style="Muted.TLabel").pack(anchor="w", pady=(3, 0))

    # ---- timed reminder ---------------------------------------------
    app.portal_panel = _group(root, "官网入口与到点提醒（不会自动提交）")
    app.portal_panel.pack(fill="x", padx=8, pady=(0, 6))
    reminder_row = ttk.Frame(app.portal_panel)
    reminder_row.pack(fill="x")
    app.portal_appointment_button = _button(
        reminder_row, "设置到点打开官网", app.toggle_portal_appointment)
    ttk.Label(
        reminder_row,
        text="说明：官网到点提醒与“软件内直接接口提交”不是同一功能。",
        style="Muted.TLabel").pack(side="left", padx=(10, 0))
    _status(app.portal_panel, app.portal_appointment_status).pack(
        anchor="w", pady=(3, 0))


def build_tools_page(app) -> None:
    """Developer / read-only tools; no network call at widget creation."""
    root = app.__dict__.get("_tools_content", app.tab_tools)
    ttk.Label(
        root, text="以下按钮均为本机诊断或只读核验，不会证明学校写接口已接通。",
        style="Muted.TLabel").pack(anchor="w", padx=8, pady=(6, 3))

    # ---- local diagnostics ------------------------------------------
    app.tools_operations = _group(root, "本机诊断（不发起选课提交）")
    app.tools_operations.pack(fill="x", padx=6, pady=(0, 3))
    for group in (
        (("检查本机配置", app.check_config),
         ("登录 HAR 分析（离线）", app.audit_auth_route_har),
         ("课程 HAR 分析（离线）", app.audit_local_har)),
        (("选课表头 HTML 核验（离线）", app.inspect_elective_table_file),
         ("课程目录证据对照", app.review_catalog_evidence),
         ("离线 HTML 演练", app.replay_local)),
        (("虚构示例（仅演示）", app.demo_next),
         ("本机离线调度测速", app.benchmark_offline),
         ("查看学校官方入口", app.show_official_entries),
         ("检测官方登录页可达性", app.probe_official_entries),
         ("展开 / 收起高级配置", app.toggle_advanced_panel)),
    ):
        row = _row(app.tools_operations)
        for label, callback in group:
            _button(row, label, callback)
    # Kept as a public reference for GUI tests and automation.
    app.scheduled_preflight_button = _button(
        _row(app.tools_operations), "检查自动选课计划（离线）",
        app.inspect_scheduled_runner)

    # ---- read-only school verification ------------------------------
    verify = _group(root, "学校接口只读核验")
    verify.pack(fill="x", padx=6, pady=ROW_GAP)
    verify.columnconfigure(1, weight=1)
    for index, (button_attr, label, callback, variable) in enumerate((
        ("school_page_button", "核验已选课程结果网页（只读）",
         app.check_known_school_results, app.school_page_status),
        ("s202_result_button", "读取华水 S202 已选结果（只读）",
         app.check_s202_selected_results, app.s202_result_status),
        ("course_plan_button", "读取本学期开课计划（只读快照）",
         app.check_course_plan, app.course_plan_status),
        ("s202_round_button", "核对当前 S202 轮次（只读）",
         app.check_s202_round, app.s202_round_status),
        ("s202_score_button", "核对本人已选门数（只读）",
         app.check_personal_s202_score, app.s202_score_status),
        ("term_probe_button", "核验学期接口（已抓包 · 只读）",
         app.check_known_year_term, app.term_probe_status),
    )):
        button = ttk.Button(verify, text=label, command=callback)
        button.grid(row=index, column=0, sticky="ew", padx=2, pady=1)
        setattr(app, button_attr, button)
        _status(verify, variable, wraplength=700).grid(
            row=index, column=1, sticky="w", padx=(8, 0), pady=1)

    # The two remaining rows keep the same two-column rhythm.
    lesson_row = ttk.Frame(verify)
    lesson_row.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(4, 0))
    ttk.Label(lesson_row, text="官网学期").pack(side="left", padx=(0, 4))
    ttk.Entry(
        lesson_row, textvariable=app.lesson_detail_semester, width=14,
    ).pack(side="left", padx=(0, 6))
    app.lesson_detail_button = ttk.Button(
        lesson_row, text="读取本人教务课表 AJAX（只读）",
        command=app.check_lesson_detail)
    app.lesson_detail_button.pack(side="left")
    _status(lesson_row, app.lesson_detail_status, wraplength=560).pack(
        side="left", fill="x", expand=True, padx=(8, 0))

    contract_row = ttk.Frame(verify)
    contract_row.grid(row=7, column=0, columnspan=2, sticky="ew", pady=(4, 0))
    _button(contract_row, "核验初始化／资格预检 HAR（离线）",
            app.inspect_ncwu_contracts)
    _button(contract_row, "复制脱敏契约报告", app.copy_ncwu_contract_report)

    # ---- advanced protocol config (hidden until toggled) ------------
    app.advanced_panel = _group(root, "高级协议配置 · 仅适合调试")
    ttk.Label(app.advanced_panel, text="配置文件").pack(side="left")
    app.config_entry = ttk.Entry(app.advanced_panel, textvariable=app.path)
    app.config_entry.pack(side="left", fill="x", expand=True, padx=(6, 6))
    app.pick_button = ttk.Button(
        app.advanced_panel, text="选择…", command=app.pick_config)
    app.pick_button.pack(side="left")
    app.open_button = ttk.Button(
        app.advanced_panel, text="打开文件", command=app.open_config)
    app.open_button.pack(side="left", padx=(5, 0))

    # ---- run log ----------------------------------------------------
    app.log_frame = _group(root, "运行日志（不显示账号或凭证）")
    app.log_frame.pack(fill="both", expand=True, padx=6, pady=(2, 6))
    log_wrap = ttk.Frame(app.log_frame)
    log_wrap.pack(fill="both", expand=True)
    app.log_box = tk.Text(
        log_wrap, height=6, state="disabled", wrap="none",
        font=FONT_MONO, bg=PALETTE["surface"], fg=PALETTE["ink"],
        relief="solid", borderwidth=1, highlightthickness=0,
        insertbackground=PALETTE["ink"], padx=6, pady=4,
    )
    log_bar = ttk.Scrollbar(
        log_wrap, orient="vertical", command=app.log_box.yview)
    app.log_box.configure(yscrollcommand=log_bar.set)
    log_bar.pack(side="right", fill="y")
    app.log_box.pack(side="left", fill="both", expand=True)
    footer = ttk.Frame(app.log_frame)
    footer.pack(fill="x", pady=(4, 0))
    ttk.Label(
        footer,
        text="日志仅本机可见，滚动条与导出按钮不会上传任何内容",
        style="Muted.TLabel").pack(side="left")
    _button(footer, "导出本机运行日志", app.export_log, side="right")
