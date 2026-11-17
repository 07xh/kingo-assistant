"""Independent KingoAssistant Tk page layouts.

Only creates widgets and binds existing Application callbacks and StringVar
instances. The Application owns all control attributes, the event queue,
session state, configuration, and background worker lifecycle. Keep these
two user journeys in one module to avoid a collection of tiny UI files.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk


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
    # Diagnostics, examples, protocol editing and logs are only in this
    # tertiary tab; no simulation button on the everyday main screen.
    tools_heading = ttk.Frame(app.tab_tools, padding=(12, 8))
    tools_heading.pack(fill="x")
    ttk.Label(
        tools_heading,
        text="这些是开发诊断和本地测试工具，不会证明学校接口已经接通。",
        foreground="#666",
    ).pack(anchor="w")
    app.tools_operations = ttk.LabelFrame(
        app.tab_tools, text="本机诊断（不发起选课提交）", padding=10)
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
        app.tab_tools, text="高级协议配置 · 仅适合调试",
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
        app.tab_tools, text="本地运行日志（不显示账号或凭证）", padding=8)
    app.log_frame.pack(
        fill="both", expand=True, padx=10, pady=(0, 8))
    app.log_box = tk.Text(
        app.log_frame, height=7, state="disabled", wrap="word")
    app.log_box.pack(fill="both", expand=True)
    ttk.Button(
        app.log_frame, text="导出本机运行日志",
        command=app.export_log,
    ).pack(anchor="e", pady=(5, 0))
