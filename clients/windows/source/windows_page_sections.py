"""Independent KingoAssistant Tk page layouts.

Only creates widgets and binds existing Application callbacks and StringVar
instances. The Application owns all control attributes, the event queue,
session state, configuration, and background worker lifecycle. Keep the
views in one stateless module.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from windows_readonly_widgets import _create_readonly_tree
from windows_visual_shell import FONT_SMALL, PALETTE, display_scale


def _table_panel(parent, columns, titles, widths, *, height):
    frame = ttk.Frame(parent)
    frame.pack(fill="both", expand=True)
    frame.columnconfigure(0, weight=1)
    frame.rowconfigure(0, weight=1)
    scale = display_scale(parent)
    table = _create_readonly_tree(
        frame, columns=columns, titles=titles,
        widths=tuple(round(width * scale) for width in widths),
        minwidth=45, height=height,
    )
    for column in columns:
        table.heading(column, anchor="w")
    vertical = ttk.Scrollbar(frame, orient="vertical", command=table.yview)
    horizontal = ttk.Scrollbar(frame, orient="horizontal", command=table.xview)
    table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
    table.grid(row=0, column=0, sticky="nsew")
    vertical.grid(row=0, column=1, sticky="ns")
    horizontal.grid(row=1, column=0, sticky="ew")
    return table


def build_home_page(app) -> None:
    """Keep consultation, targets, log and execution visible together."""
    body = ttk.Frame(app.tab_home, padding=(6, 5))
    body.pack(fill="both", expand=True)
    scale = display_scale(app)
    body.rowconfigure(1, weight=3, minsize=round(53 * scale))
    body.rowconfigure(3, weight=2, minsize=round(184 * scale))
    body.columnconfigure(0, weight=1)

    query = ttk.Frame(body)
    query.grid(row=0, column=0, sticky="ew", pady=(0, 4))
    app.course_filter = tk.StringVar(master=app)
    app.teacher_filter = tk.StringVar(master=app)
    for label, variable, width in (
        ("课程 / 编号", app.course_filter, 20),
        ("教师", app.teacher_filter, 13),
    ):
        ttk.Label(query, text=label).pack(side="left", padx=(0, 4))
        ttk.Entry(query, textvariable=variable, width=width).pack(
            side="left", padx=(0, 8))
        variable.trace_add("write", app.render_catalog_rows)

    app.add_catalog_course_button = ttk.Button(
        query, text="加入目标", command=app.add_catalog_course, state="disabled")
    app.add_catalog_course_button.pack(side="right")
    app.live_catalog_button = ttk.Button(
        query, text="查询课程", command=app.check_live_catalog)
    app.live_catalog_button.pack(side="right", padx=(0, 5))
    app.live_poll_button = ttk.Button(
        query, text="自动刷新", command=app.toggle_live_polling)
    app.live_poll_button.pack(side="right", padx=(0, 5))

    catalog = ttk.Frame(body)
    catalog.grid(row=1, column=0, sticky="nsew")
    app._live_catalog_tree = _table_panel(
        catalog, ("name", "code", "class_number", "teacher", "selection_status", "points", "target"),
        ("课程名称", "课程代码", "显示班号", "教师", "选课状态", "选课币", "本地目标"),
        (188, 103, 82, 92, 110, 64, 115), height=6,
    )
    app._live_catalog_tree.bind("<<TreeviewSelect>>", app.update_catalog_selection)

    summary = ttk.Frame(body)
    summary.grid(row=2, column=0, sticky="ew", pady=(4, 5))
    app.catalog_count = tk.StringVar(master=app, value="显示 0 门")
    ttk.Label(summary, textvariable=app.catalog_count, style="Muted.TLabel").pack(side="left")
    app._live_target_review_button = ttk.Button(
        summary, text="候选分析", command=app.show_live_target_review, state="disabled")
    app._live_target_review_button.pack(side="right")
    ttk.Label(summary, textvariable=app.live_catalog_status,
              style="Muted.TLabel", anchor="w").pack(
                  side="left", padx=8, fill="x", expand=True)

    work = ttk.Frame(body)
    work.grid(row=3, column=0, sticky="nsew")
    work.rowconfigure(0, weight=1)
    work.columnconfigure(0, weight=5, uniform="work")
    work.columnconfigure(1, weight=3, uniform="work")
    targets = ttk.LabelFrame(work, text="目标任务", padding=4)
    targets.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
    target_commands = ttk.Frame(targets)
    target_commands.pack(side="bottom", fill="x", pady=(4, 0))
    app.edit_button = ttk.Button(
        target_commands, text="编辑目标", command=app.edit_settings)
    app.edit_button.pack(side="left")
    app.plans_button = ttk.Button(
        target_commands, text="课程方案", command=app.manage_plans)
    app.plans_button.pack(side="left", padx=4)
    app.table = _table_panel(
        targets, ("key", "course", "class_id", "teacher", "status"),
        ("志愿", "课程", "教学班", "教师", "查询状态"), (47, 101, 69, 58, 105),
        height=4,
    )
    app.table.bind("<Double-1>", lambda _event: app.copy_selected_field("course_id"))

    app.log_frame = ttk.LabelFrame(work, text="运行日志", padding=4)
    app.log_frame.grid(row=0, column=1, sticky="nsew", padx=(0, 5))
    log_scroll = ttk.Scrollbar(app.log_frame, orient="vertical")
    app.log_box = tk.Text(
        app.log_frame, width=1, height=4, state="disabled", wrap="word",
        font=FONT_SMALL, padx=5, pady=4, relief="flat",
        background=PALETTE["surface"], foreground=PALETTE["ink"],
        yscrollcommand=log_scroll.set,
    )
    log_scroll.configure(command=app.log_box.yview)
    log_scroll.pack(side="right", fill="y")
    app.log_box.pack(fill="both", expand=True)

    plan = ttk.LabelFrame(work, text="执行计划", padding=5)
    plan.grid(row=0, column=2, sticky="nsew")
    app.plan_summary = tk.StringVar(master=app, value="尚未读取本机计划")
    ttk.Label(plan, textvariable=app.plan_summary, style="Muted.TLabel").pack(anchor="w")
    app.enrollment_gate_status = tk.StringVar(master=app, value=app.enrollment_gate_label())
    ttk.Label(plan, textvariable=app.enrollment_gate_status,
              style="Muted.TLabel", wraplength=round(182 * scale)).pack(
                  anchor="w", pady=(3, 2))
    app.action_panel = ttk.Frame(plan)
    app.action_panel.pack(side="bottom", fill="x")
    for col in (0, 1):
        app.action_panel.columnconfigure(col, weight=1)
    app.start_button = ttk.Button(
        app.action_panel, text="开始余量监测", command=app.start_monitor,
        style="KingoPrimary.TButton")
    app.start_button.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(3, 0))
    ttk.Button(app.action_panel, text="启动前检查", command=app.preflight).grid(
        row=2, column=0, sticky="ew", padx=(0, 3))
    app.stop_button = ttk.Button(
        app.action_panel, text="停止", command=app.stop_monitor, state="disabled")
    app.stop_button.grid(row=2, column=1, sticky="ew")

    app.enrollment_detail_button = ttk.Button(
        app.action_panel, text="提交条件", command=app.show_enrollment_gate_details)
    app.enrollment_detail_button.grid(row=0, column=0, sticky="ew", padx=(0, 3), pady=(0, 2))
    app.scheduled_start_button = ttk.Button(
        app.action_panel, text="自动提交", command=app.start_scheduled_enrollment, state="disabled")
    app.scheduled_start_button.grid(row=1, column=0, sticky="ew", padx=(0, 3), pady=(0, 2))
    app.scheduled_stop_button = ttk.Button(
        app.action_panel, text="取消自动计划", command=app.stop_scheduled_enrollment, state="disabled")
    app.scheduled_stop_button.grid(row=1, column=1, sticky="ew", pady=(0, 2))


def build_history_page(app) -> None:
    pane = ttk.LabelFrame(app.tab_history, text="本次运行记录", padding=6)
    pane.pack(fill="both", expand=True, padx=8, pady=8)
    actions = ttk.Frame(pane)
    actions.pack(side="bottom", fill="x", pady=(5, 0))
    ttk.Button(actions, text="导出运行日志", command=app.export_log).pack(side="right")
    scrollbar = ttk.Scrollbar(pane, orient="vertical")
    app.execution_log = tk.Text(
        pane, state="disabled", wrap="word", font=FONT_SMALL,
        padx=6, pady=5, relief="flat", yscrollcommand=scrollbar.set,
        background=PALETTE["surface"], foreground=PALETTE["ink"],
    )
    scrollbar.configure(command=app.execution_log.yview)
    scrollbar.pack(side="right", fill="y")
    app.execution_log.pack(fill="both", expand=True)


def build_account_page(app) -> None:
    body = ttk.Frame(app.tab_account, padding=(8, 8))
    body.pack(fill="both", expand=True)
    session = ttk.LabelFrame(body, text="当前教务会话", padding=10)
    session.pack(side="left", fill="both", expand=True, padx=(0, 6))
    login = ttk.LabelFrame(body, text="账号密码登录", padding=10)
    login.pack(side="right", fill="y")
    app.login_username = tk.StringVar(master=app)
    app.login_password = tk.StringVar(master=app)
    for row, label, variable, mask in (
        (0, "学工号", app.login_username, ""),
        (1, "密码", app.login_password, "*"),
    ):
        ttk.Label(login, text=label).grid(row=row, column=0, sticky="w", pady=4)
        entry = ttk.Entry(login, textvariable=variable, width=25, show=mask)
        entry.grid(row=row, column=1, sticky="ew", padx=(8, 0), pady=4)
        entry.bind("<Return>", lambda _event: app.start_native_login())
    app.native_login_button = ttk.Button(
        login, text="登录", command=app.start_native_login, style="KingoPrimary.TButton")
    app.native_login_button.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(5, 2))
    ttk.Label(login, text="密码仅用于本次登录", style="Muted.TLabel").grid(
        row=3, column=0, columnspan=2, sticky="w", pady=4)

    actions = ttk.Frame(session)
    actions.pack(fill="x")
    app.native_clear_button = ttk.Button(
        actions, text="清除会话", command=app.clear_native_session)
    app.native_clear_button.pack(side="left")
    app.native_prepare_button = ttk.Button(
        actions, text="核验账号与课程目录", command=app.check_native_preparation)
    app.native_prepare_button.pack(side="left", padx=5)
    ttk.Button(actions, text="官方登录入口", command=app.show_official_entries).pack(
        side="left")
    ttk.Label(session, textvariable=app.native_login_status,
              wraplength=round(360 * display_scale(app))).pack(anchor="w", pady=(9, 4))
    ttk.Label(session, textvariable=app.native_preparation_status,
              wraplength=round(360 * display_scale(app)),
              style="Muted.TLabel").pack(anchor="w", pady=(0, 6))
    app.account_table = _table_panel(
        session, ("account", "method", "state", "storage"),
        ("学工号", "登录方式", "会话状态", "会话保存"),
        (96, 110, 110, 100), height=3,
    )
    app.native_login_status.trace_add("write", app.refresh_account_view)
    app.refresh_account_view()


def build_settings_page(app) -> None:
    settings = ttk.LabelFrame(app.tab_settings, text="本机课程与监测计划", padding=10)
    settings.pack(fill="x", padx=8, pady=8)
    ttk.Label(settings, textvariable=app.plan_summary).pack(anchor="w")
    app.monitor_settings_summary = tk.StringVar(master=app)
    ttk.Label(settings, textvariable=app.monitor_settings_summary).pack(anchor="w", pady=4)
    ttk.Label(settings, textvariable=app.monitor_cadence_status,
              style="Muted.TLabel").pack(anchor="w", pady=4)
    actions = ttk.Frame(settings)
    actions.pack(fill="x", pady=(2, 0))
    for label, command in (
        ("编辑时间与监测参数", app.edit_settings),
        ("管理课程方案", app.manage_plans),
        ("高级配置", lambda: app.toggle_advanced_panel(show=True)),
    ):
        ttk.Button(actions, text=label, command=command).pack(side="left", padx=(0, 5))


def build_tools_page(app) -> None:
    """Developer/read-only tools; no network calls at widget creation time."""
    tools_root = app.__dict__.get("_tools_content", app.tab_tools)
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
    timetable_row = ttk.Frame(app.tools_operations)
    timetable_row.pack(fill="x", pady=4)
    app.timetable_read_button = ttk.Button(
        timetable_row, text="读取本人课表（既有接口 · 只读）",
        command=app.check_known_timetable)
    app.timetable_read_button.pack(side="left", padx=(0, 8))
    ttk.Label(timetable_row, textvariable=app.timetable_read_status,
              style="Muted.TLabel", wraplength=610).pack(
                  side="left", fill="x", expand=True)
    # Native API session check is available under Tools, not a browser page.
    app.session_check_button = ttk.Button(
        app.tools_operations, text="核验应用内登录会话（只读）",
        command=app.check_script_session,
    )
    app.session_check_button.pack(anchor="w", pady=(3, 0))
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
    ).pack(side="left", padx=(0, 8))
    ttk.Button(
        contract_row, text="核验提交 HAR（离线）",
        command=app.inspect_ncwu_submit_har,
    ).pack(side="left")
    static_contract_row = ttk.Frame(app.tools_operations)
    static_contract_row.pack(fill="x", pady=(4, 1))
    ttk.Button(
        static_contract_row, text="核对提交 HTML／JS（离线）",
        command=app.inspect_ncwu_static_submit_files,
    ).pack(side="left", padx=(0, 8))
    ttk.Label(
        static_contract_row,
        text="仅分析已保存文件；不扫描学校路径、不提交请求",
        foreground="#666", wraplength=650,
    ).pack(side="left", fill="x", expand=True)

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
