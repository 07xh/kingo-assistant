"""Offline course/strategy editor for the Windows desktop application."""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from core.desktop_settings import (COURSE_FIELDS, INTERVAL_CUSTOM, INTERVAL_PRESETS,
                                   interval_preset_for, parse_interval_range,
                                   load_config, normalize_course, save_config_atomic,
                                   update_config)
from main import validate


class CourseDialog(simpledialog.Dialog):
    def __init__(self, parent, existing: dict | None = None):
        self.existing = deepcopy(existing or {})
        self.values = {}
        self.result = None
        super().__init__(parent, title="编辑课程" if existing else "新增课程")

    def body(self, frame):
        labels = {"key": "志愿标识 *", "course_id": "课程代码",
                  "class_id": "教学班编号", "course_name": "课程名称",
                  "teacher": "任课教师"}
        first = None
        for index, name in enumerate(COURSE_FIELDS):
            ttk.Label(frame, text=labels[name]).grid(row=index, column=0, sticky="w", padx=(8, 6), pady=7)
            value = tk.StringVar(value=str(self.existing.get(name) or ""))
            entry = ttk.Entry(frame, textvariable=value, width=42)
            entry.grid(row=index, column=1, sticky="ew", padx=6, pady=7)
            self.values[name] = value
            if first is None:
                first = entry
        ttk.Label(frame, text="至少提供课程代码、课程名称或教师；相同课程可用教学班编号区分。",
                  foreground="#666", wraplength=360).grid(row=len(COURSE_FIELDS), column=0,
                                                        columnspan=2, sticky="w", padx=8, pady=(6, 10))
        return first

    def validate(self):
        try:
            self.result = normalize_course({**self.existing,
                                            **{name: var.get() for name, var in self.values.items()}})
            return True
        except ValueError as exc:
            messagebox.showerror("课程资料无效", str(exc), parent=self)
            return False


class SettingsEditor(tk.Toplevel):
    """Edit normal course settings; cannot set protocol.verified or credentials."""

    def __init__(self, parent: tk.Misc, path: Path, on_saved):
        config = load_config(path)
        validate(config, check_only=True)
        super().__init__(parent)
        self.title("编辑课程与监测设置（仅本机）")
        self.geometry("800x590")
        self.minsize(690, 510)
        self.transient(parent)
        self.path = path
        self.config_data = config
        self.on_saved = on_saved
        self.courses = deepcopy(config["courses"])
        strategy = config["strategy"]
        self.semester = tk.StringVar(value=str(config["xnxq"]))
        self.mode = tk.StringVar(value=str(strategy["mode"]))
        self.target_time = tk.StringVar(value=str(strategy["target_time"]))
        self.interval_low = tk.StringVar(value=str(strategy["interval_range"][0]))
        self.interval_high = tk.StringVar(value=str(strategy["interval_range"][1]))
        self.interval_preset = tk.StringVar(value=interval_preset_for(
            self.interval_low.get(), self.interval_high.get()))
        self.interval_low.trace_add("write", self._on_interval_edited)
        self.interval_high.trace_add("write", self._on_interval_edited)
        self.max_cycles = tk.StringVar(value=str(strategy["max_cycles"]))
        self._build()
        self.refresh_table()
        self.grab_set()

    def _build(self):
        outer = ttk.Frame(self, padding=14)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="本机课程与监测配置", font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w")
        ttk.Label(outer, text="仅修改本机 YAML；不会连接学校。真实接口必须另外核实。",
                  foreground="#666").pack(anchor="w", pady=(4, 8))

        settings = ttk.LabelFrame(outer, text="基本选项", padding=10)
        settings.pack(fill="x")
        for col in range(4):
            settings.columnconfigure(col, weight=1 if col % 2 else 0)
        fields = [
            ("学年学期", self.semester, 0, 0),
            ("计划检查时间", self.target_time, 0, 2),
            ("间隔下限（秒）", self.interval_low, 3, 0),
            ("间隔上限（秒）", self.interval_high, 3, 2),
            ("最多查询次数", self.max_cycles, 4, 0),
        ]
        ttk.Label(settings, text="查询模式").grid(row=1, column=0, sticky="w", pady=5)
        ttk.Combobox(settings, textvariable=self.mode,
                     values=("monitor", "burst"), state="readonly", width=22).grid(
                         row=1, column=1, sticky="ew", padx=6, pady=5)
        ttk.Label(settings, text="monitor=只读监测；burst=到点只查询一次，不会自动选课。",
                  foreground="#666").grid(row=1, column=2, columnspan=2, sticky="w", padx=6)
        ttk.Label(settings, text="监测间隔方案").grid(row=2, column=0, sticky="w", pady=5)
        presets = ttk.Combobox(settings, textvariable=self.interval_preset,
                               values=(*INTERVAL_PRESETS, INTERVAL_CUSTOM),
                               state="readonly", width=22)
        presets.grid(row=2, column=1, sticky="ew", padx=6, pady=5)
        presets.bind("<<ComboboxSelected>>", self._apply_interval_preset)
        ttk.Label(settings, text="常规 60–3600 秒；0.5/1/1.5 秒需校方明确许可。",
                  foreground="#666").grid(row=2, column=2, columnspan=2,
                                           sticky="w", padx=6)
        for label, var, row, col in fields:
            ttk.Label(settings, text=label).grid(row=row, column=col, sticky="w", pady=5)
            ttk.Entry(settings, textvariable=var, width=23).grid(
                row=row, column=col + 1, sticky="ew", padx=6, pady=5)
        ttk.Label(settings, text="默认 60 秒不是学校允许的最高频率。低于 60 秒须在配置中记录校方书面许可；不符合许可将拒绝保存和启动。单链路串行查询，无并发突发。",
                  foreground="#666", wraplength=720).grid(
                      row=5, column=0, columnspan=4, sticky="w", pady=(7, 2))

        wrapper = ttk.LabelFrame(outer, text="目标课程（1–30 门）", padding=8)
        wrapper.pack(fill="both", expand=True, pady=(12, 6))
        self.table = ttk.Treeview(wrapper, columns=("key", "code", "class", "name", "teacher"), show="headings", height=8)
        for name, label, width in (("key", "志愿", 80), ("code", "课程代码", 140),
                                    ("class", "教学班", 110), ("name", "课程名称", 190),
                                    ("teacher", "教师", 110)):
            self.table.heading(name, text=label)
            self.table.column(name, width=width)
        bar = ttk.Scrollbar(wrapper, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.table.pack(fill="both", expand=True)
        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(0, 10))
        ttk.Button(buttons, text="新增课程", command=self.add_course).pack(side="left", padx=(0, 7))
        ttk.Button(buttons, text="编辑所选", command=self.edit_course).pack(side="left", padx=(0, 7))
        ttk.Button(buttons, text="删除所选", command=self.remove_course).pack(side="left")
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="保存到配置文件", command=self.save).pack(side="right", padx=8)
        ttk.Label(outer, text="保存时会清除 YAML 中的 Cookie 和推送令牌。若需使用，应通过环境变量提供。",
                  foreground="#666", wraplength=730).pack(anchor="w")
        self.table.bind("<Double-1>", lambda _: self.edit_course())

    def _on_interval_edited(self, *_):
        self.interval_preset.set(interval_preset_for(
            self.interval_low.get(), self.interval_high.get()))

    def _apply_interval_preset(self, _event=None):
        choice = self.interval_preset.get()
        if choice in INTERVAL_PRESETS:
            low, high = INTERVAL_PRESETS[choice]
            self.interval_low.set(str(low))
            self.interval_high.set(str(high))

    def refresh_table(self):
        self.table.delete(*self.table.get_children())
        for i, course in enumerate(self.courses):
            self.table.insert("", "end", iid=str(i), values=(
                course.get("key", ""), course.get("course_id", ""), course.get("class_id", ""),
                course.get("course_name", ""), course.get("teacher", "")))

    def selection(self) -> int | None:
        selection = self.table.selection()
        return int(selection[0]) if selection else None

    def add_course(self):
        if len(self.courses) >= 30:
            messagebox.showwarning("数量已达上限", "最多配置 30 门课程", parent=self)
            return
        dialog = CourseDialog(self)
        if dialog.result:
            self.courses.append(dialog.result)
            self.refresh_table()

    def edit_course(self):
        i = self.selection()
        if i is None:
            messagebox.showinfo("请选择课程", "先在表格中选择要编辑的课程。", parent=self)
            return
        dialog = CourseDialog(self, self.courses[i])
        if dialog.result:
            self.courses[i] = dialog.result
            self.refresh_table()

    def remove_course(self):
        i = self.selection()
        if i is not None:
            if len(self.courses) == 1:
                messagebox.showwarning("至少保留一门", "请至少配置一门目标课程。", parent=self)
                return
            self.courses.pop(i)
            self.refresh_table()

    def save(self):
        try:
            interval_low, interval_high = parse_interval_range(
                self.interval_low.get(), self.interval_high.get())
            revised = update_config(
                self.config_data, courses=self.courses, semester=self.semester.get(),
                mode=self.mode.get(), target_time=self.target_time.get(),
                interval_low=interval_low, interval_high=interval_high,
                max_cycles=int(self.max_cycles.get()))
            redacted = save_config_atomic(self.path, revised)
        except (ValueError, OSError, KeyError) as exc:
            messagebox.showerror("无法保存", str(exc), parent=self)
            return
        self.on_saved()
        message = "课程与设置已保存。未修改官方协议验证状态。"
        if redacted:
            message += "\n已从 YAML 清除敏感字段：" + "、".join(redacted)
        messagebox.showinfo("保存成功", message, parent=self)
        self.destroy()
