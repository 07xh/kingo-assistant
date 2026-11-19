"""Independent KingoAssistant Tk page layouts.

Only creates widgets and binds existing Application callbacks and StringVar
instances. The Application owns all control attributes, the event queue,
session state, configuration, and background worker lifecycle. Keep these
two user journeys in one module to avoid a collection of tiny UI files.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk


def build_home_page(app) -> None:
    """Construct a compact, Bypass-inspired workbench around existing callbacks.

    The view owns no school state or business logic. All actions continue to
    call the original Application methods; no network work starts at render.
    """
    home_root = app.__dict__.get("_home_content", app.tab_home)

    intro = ttk.Frame(home_root, padding=(10, 5))
    intro.pack(fill="x")
    ttk.Label(
        intro, text="课程工作台",
        font=("Microsoft YaHei UI", 12, "bold"),
    ).pack(anchor="w")
    ttk.Label(
        intro,
        text="目标课程、本人账号、只读查询与监测集中在一个页面；自动提交尚未接入。",
        foreground="#64748B",
    ).pack(anchor="w", pady=(2, 0))

    # Three compact control groups share one toolbar row. This replaces the
    # previous full-width vertical step cards while retaining their callbacks.
    toolbar = ttk.Frame(home_root, padding=(10, 3))
    toolbar.pack(fill="x")
    for column, weight in enumerate((1, 2, 1)):
        toolbar.grid_columnconfigure(column, weight=weight, uniform="workbench")

    setup = ttk.LabelFrame(
        toolbar, text="课程方案", padding=(8, 6),
        style="KingoStep.TLabelframe",
    )
    setup.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
    app.edit_button = ttk.Button(
        setup, text="编辑目标课程", command=app.edit_settings,
        style="KingoPrimary.TButton",
    )
    app.edit_button.pack(fill="x", pady=(0, 5))
    app.plans_button = ttk.Button(
        setup, text="课程方案", command=app.manage_plans,
    )
    app.plans_button.pack(fill="x")
    ttk.Label(
        setup, text="本机保存 · 不会提交学校",
        foreground="#64748B", wraplength=190,
    ).pack(anchor="w", pady=(6, 0))

    auth_panel = ttk.LabelFrame(
        toolbar, text="账号与数据", padding=(8, 6),
        style="KingoStep.TLabelframe",
    )
    auth_panel.grid(row=0, column=1, sticky="nsew", padx=3)
    auth_actions = ttk.Frame(auth_panel)
    auth_actions.pack(fill="x")
    app.native_login_button = ttk.Button(
        auth_actions, text="登录教务账号", command=app.start_native_login,
        style="KingoPrimary.TButton",
    )
    app.native_login_button.pack(side="left", padx=(0, 5), fill="x", expand=True)
    app.native_prepare_button = ttk.Button(
        auth_actions, text="核验登录与数据",
        command=app.check_native_preparation,
    )
    app.native_prepare_button.pack(side="left", padx=5, fill="x", expand=True)
    app.native_clear_button = ttk.Button(
        auth_actions, text="清除登录", command=app.clear_native_session,
    )
    app.native_clear_button.pack(side="left", padx=(5, 0))

    ttk.Label(
        auth_panel, textvariable=app.native_login_status,
        foreground="#64748B", wraplength=410,
    ).pack(anchor="w", pady=(6, 1))
    ttk.Label(
        auth_panel, textvariable=app.native_preparation_status,
        foreground="#64748B", wraplength=410,
    ).pack(anchor="w")

    data_actions = ttk.Frame(auth_panel)
    data_actions.pack(fill="x", pady=(7, 0))
    app.timetable_read_button = ttk.Button(
        data_actions, text="读取本人课表", command=app.check_known_timetable,
    )
    app.timetable_read_button.pack(side="left", padx=(0, 5), fill="x", expand=True)
    app.live_catalog_button = ttk.Button(
        data_actions, text="读取 S202 目录", command=app.check_live_catalog,
    )
    app.live_catalog_button.pack(side="left", padx=5, fill="x", expand=True)
    app.live_poll_button = ttk.Button(
        data_actions, text="自动刷新", command=app.toggle_live_polling,
    )
    app.live_poll_button.pack(side="left", padx=(5, 0), fill="x", expand=True)
    ttk.Label(
        auth_panel, textvariable=app.timetable_read_status,
        foreground="#64748B", wraplength=410,
    ).pack(anchor="w", pady=(5, 0))
    ttk.Label(
        auth_panel, textvariable=app.live_catalog_status,
        foreground="#416075", wraplength=410,
    ).pack(anchor="w", pady=(2, 0))

    monitor_panel = ttk.LabelFrame(
        toolbar, text="监测控制", padding=(8, 6),
        style="KingoStep.TLabelframe",
    )
    monitor_panel.grid(row=0, column=2, sticky="nsew", padx=(6, 0))
    app.action_panel = ttk.Frame(monitor_panel)
    app.action_panel.pack(fill="x")
    app.start_button = ttk.Button(
        app.action_panel, text="开始只读监测", command=app.start_monitor,
        style="KingoPrimary.TButton",
    )
    app.start_button.pack(fill="x", pady=(0, 5))
    app.stop_button = ttk.Button(
        app.action_panel, text="停止监测", command=app.stop_monitor,
        state="disabled",
    )
    app.stop_button.pack(fill="x", pady=(0, 5))
    ttk.Button(
        app.action_panel, text="离线预检", command=app.preflight,
    ).pack(fill="x")

    app.enrollment_gate_status = tk.StringVar(value=app.enrollment_gate_label())
    ttk.Label(
        monitor_panel, textvariable=app.enrollment_gate_status,
        foreground="#965B16", wraplength=205,
    ).pack(anchor="w", pady=(7, 4))
    app.scheduled_start_button = ttk.Button(
        monitor_panel, text="自动提交（未接入）",
        command=app.start_scheduled_enrollment, state="disabled",
    )
    app.scheduled_start_button.pack(fill="x", pady=(0, 5))
    app.scheduled_stop_button = ttk.Button(
        monitor_panel, text="取消计划", command=app.stop_scheduled_enrollment,
        state="disabled",
    )
    app.scheduled_stop_button.pack(fill="x")

    app.course_frame = ttk.LabelFrame(
        home_root, text="目标课程列表 · 本地配置 / 只读状态",
        padding=7,
    )
    app.course_frame.pack(
        fill="both", expand=True, padx=10, pady=(5, 8))
    app.table = ttk.Treeview(
        app.course_frame,
        columns=("key", "course", "class_id", "teacher", "status"),
        show="headings", height=12,
    )
    for col, label, width in (
        ("key", "志愿", 70), ("course", "课程 / 代码", 225),
        ("class_id", "教学班", 110), ("teacher", "任课教师", 120),
        ("status", "监测状态", 240),
    ):
        app.table.heading(col, text=label)
        app.table.column(col, width=width, stretch=True)
    scroll = ttk.Scrollbar(
        app.course_frame, orient="vertical", command=app.table.yview)
    app.table.configure(yscrollcommand=scroll.set)
    scroll.pack(side="right", fill="y")
    app.table.pack(fill="both", expand=True)
    app.table.bind(
        "<Double-1>", lambda _event: app.copy_selected_field("course_id"))
    app.table.bind(
        "<Control-c>", lambda _event: app.copy_selected_field("course_id"))
    ttk.Label(
        app.course_frame,
        text="双击或按 Ctrl+C 复制课程代码。示例课程不会伪装成学校实时数据。",
        foreground="#64748B",
    ).pack(anchor="w", pady=(5, 0))


def build_manual_page(app) -> None:
    """Manual official-site fallback, deliberately separate from the EXE worker."""
    # Manual route is deliberately isolated from both read-only queries
    # and automatic enrollment. It may open the official browser.
    ttk.Label(
        app.tab_manual,
        text="手动备用：只是打开华水官方网页，登录、选课和提交都由你自己完成。",
        foreground="#965b16", wraplength=950, padding=(14, 14),
    ).pack(fill="x")
    app.portal_panel = ttk.LabelFrame(
        app.tab_manual, text="官网操作与按时提醒（不会自动提交）",
        padding=12,
    )
    app.portal_panel.pack(fill="x", padx=14, pady=(0, 10))
    app.portal_appointment_button = ttk.Button(
        app.portal_panel, text="设置到点打开官网",
        command=app.toggle_portal_appointment,
    )
    app.portal_appointment_button.pack(anchor="w", pady=(0, 8))
    manual_actions = ttk.Frame(app.portal_panel)
    manual_actions.pack(fill="x")
    ttk.Button(
        manual_actions, text="现在打开华水官网",
        command=app.open_official_portal,
    ).pack(side="left", padx=(0, 8))
    ttk.Button(
        manual_actions, text="复制所选课程代码并打开官网",
        command=app.prepare_selected_course,
    ).pack(side="left", padx=(0, 8))
    app.session_check_button = ttk.Button(
        manual_actions, text="检查软件登录状态（只读）",
        command=app.check_script_session,
    )
    app.session_check_button.pack(side="left")
    ttk.Label(
        app.portal_panel, textvariable=app.portal_appointment_status,
        foreground="#666", wraplength=950,
    ).pack(anchor="w", pady=(10, 0))
    ttk.Label(
        app.tab_manual,
        text="说明：手动官网预约与“EXE 直接接口提交”不是同一功能。",
        foreground="#666", padding=(14, 3),
    ).pack(anchor="w")


def build_tools_page(app) -> None:
    """Developer/read-only tools; no network calls at widget creation time."""
    tools_root = app.__dict__.get("_tools_content", app.tab_tools)
    # Diagnostics, examples, protocol editing and logs are only in this
    # tertiary tab; no simulation button on the everyday main screen.
    tools_heading = ttk.Frame(tools_root, padding=(12, 8))
    tools_heading.pack(fill="x")
    ttk.Label(
        tools_heading,
        text="这些是开发诊断和本地测试工具，不会证明学校接口已经接通。",
        foreground="#666",
    ).pack(anchor="w")
    app.tools_operations = ttk.LabelFrame(
        tools_root, text="本机诊断（不发起选课提交）", padding=10)
    app.tools_operations.pack(fill="x", padx=10, pady=(2, 6))
    groups = (
        (("检查本机配置", app.check_config),
         ("登录 HAR 分析（离线）", app.audit_auth_route_har),
         ("课程 HAR 分析（离线）", app.audit_local_har)),
        (("选课表头 HTML 核验（离线）", app.inspect_elective_table_file),
         ("课程目录证据对照", app.review_catalog_evidence),
         ("离线 HTML 演练", app.replay_local),
         ("虚构示例（仅演示）", app.demo_next),
         ("本机离线调度测速", app.benchmark_offline)),
        (("查看学校官方入口", app.show_official_entries),
         ("检测官方登录页可达性", app.probe_official_entries),
         ("展开 / 收起高级配置", app.toggle_advanced_panel)),
    )
    for group in groups:
        row = ttk.Frame(app.tools_operations)
        row.pack(fill="x", pady=3)
        for label, callback in group:
            ttk.Button(row, text=label, command=callback).pack(
                side="left", padx=(0, 8))
    # Keep the existing public reference for GUI tests and automation.
    app.scheduled_preflight_button = ttk.Button(
        app.tools_operations, text="检查自动选课计划（离线）",
        command=app.inspect_scheduled_runner)
    app.scheduled_preflight_button.pack(anchor="w", pady=(3, 0))
    # Readback evidence is separate from an elective-catalog query.
    result_row = ttk.Frame(app.tools_operations)
    result_row.pack(fill="x", pady=(5, 1))
    app.school_page_button = ttk.Button(
        result_row, text="核验已选课程结果网页（只读）",
        command=app.check_known_school_results,
    )
    app.school_page_button.pack(side="left", padx=(0, 8))
    ttk.Label(result_row, textvariable=app.school_page_status,
              foreground="#666", wraplength=670).pack(side="left", fill="x", expand=True)
    s202_result_row = ttk.Frame(app.tools_operations)
    s202_result_row.pack(fill="x", pady=(4, 1))
    app.s202_result_button = ttk.Button(
        s202_result_row, text="读取华水 S202 已选结果（只读）",
        command=app.check_s202_selected_results,
    )
    app.s202_result_button.pack(side="left", padx=(0, 8))
    ttk.Label(s202_result_row, textvariable=app.s202_result_status,
              foreground="#666", wraplength=670).pack(
                  side="left", fill="x", expand=True)
    plan_row = ttk.Frame(app.tools_operations)
    plan_row.pack(fill="x", pady=(4, 1))
    app.course_plan_button = ttk.Button(
        plan_row, text="读取本学期开课计划（只读快照）",
        command=app.check_course_plan,
    )
    app.course_plan_button.pack(side="left", padx=(0, 8))
    ttk.Label(
        plan_row, textvariable=app.course_plan_status,
        foreground="#666", wraplength=670,
    ).pack(side="left", fill="x", expand=True)
    lesson_row = ttk.Frame(app.tools_operations)
    lesson_row.pack(fill="x", pady=(4, 1))
    ttk.Label(lesson_row, text="官网学期：").pack(side="left")
    ttk.Entry(
        lesson_row, textvariable=app.lesson_detail_semester,
        width=16,
    ).pack(side="left", padx=(0, 8))
    app.lesson_detail_button = ttk.Button(
        lesson_row, text="读取本人教务课表 AJAX（只读）",
        command=app.check_lesson_detail,
    )
    app.lesson_detail_button.pack(side="left", padx=(0, 8))
    ttk.Label(
        lesson_row, textvariable=app.lesson_detail_status,
        foreground="#666", wraplength=480,
    ).pack(side="left", fill="x", expand=True)
    round_row = ttk.Frame(app.tools_operations)
    round_row.pack(fill="x", pady=(4, 1))
    app.s202_round_button = ttk.Button(
        round_row, text="核对当前 S202 轮次（只读）",
        command=app.check_s202_round,
    )
    app.s202_round_button.pack(side="left", padx=(0, 8))
    ttk.Label(round_row, textvariable=app.s202_round_status,
              foreground="#666", wraplength=670).pack(
                  side="left", fill="x", expand=True)
    score_row = ttk.Frame(app.tools_operations)
    score_row.pack(fill="x", pady=(4, 1))
    app.s202_score_button = ttk.Button(
        score_row, text="核对本人已选门数（只读）",
        command=app.check_personal_s202_score,
    )
    app.s202_score_button.pack(side="left", padx=(0, 8))
    ttk.Label(
        score_row, textvariable=app.s202_score_status,
        foreground="#666", wraplength=670,
    ).pack(side="left", fill="x", expand=True)
    contract_row = ttk.Frame(app.tools_operations)
    contract_row.pack(fill="x", pady=(4, 1))
    ttk.Button(
        contract_row, text="核验初始化／资格预检 HAR（离线）",
        command=app.inspect_ncwu_contracts,
    ).pack(side="left", padx=(0, 8))
    ttk.Button(
        contract_row, text="复制脱敏契约报告",
        command=app.copy_ncwu_contract_report,
    ).pack(side="left")
    term_row = ttk.Frame(app.tools_operations)
    term_row.pack(fill="x", pady=(4, 1))
    app.term_probe_button = ttk.Button(
        term_row, text="核验学期接口（已抓包 · 只读）",
        command=app.check_known_year_term,
    )
    app.term_probe_button.pack(side="left", padx=(0, 8))
    ttk.Label(term_row, textvariable=app.term_probe_status,
              foreground="#666", wraplength=670).pack(side="left", fill="x", expand=True)

    app.advanced_panel = ttk.LabelFrame(
        tools_root, text="高级协议配置 · 仅适合调试",
        padding=8,
    )
    ttk.Label(app.advanced_panel, text="配置文件").pack(side="left")
    app.config_entry = ttk.Entry(
        app.advanced_panel, textvariable=app.path)
    app.config_entry.pack(side="left", fill="x", expand=True, padx=(8, 6))
    app.pick_button = ttk.Button(
        app.advanced_panel, text="选择…", command=app.pick_config)
    app.pick_button.pack(side="left")
    app.open_button = ttk.Button(
        app.advanced_panel, text="打开文件", command=app.open_config)
    app.open_button.pack(side="left", padx=5)

    app.log_frame = ttk.LabelFrame(
        tools_root, text="本地运行日志（不显示账号或凭证）", padding=8)
    app.log_frame.pack(
        fill="both", expand=True, padx=10, pady=(0, 8))
    app.log_box = tk.Text(
        app.log_frame, height=7, state="disabled", wrap="word")
    app.log_box.pack(fill="both", expand=True)
    ttk.Button(
        app.log_frame, text="导出本机运行日志",
        command=app.export_log,
    ).pack(anchor="e", pady=(5, 0))
