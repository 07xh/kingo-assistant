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
from core.conflicts import find_conflicts
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
                  "teacher": "任课教师", "group": "备选组（可选）",
                  "time_slots": "上课节次（可选）"}
        first = None
        for index, name in enumerate((*COURSE_FIELDS, "group", "time_slots")):
            ttk.Label(frame, text=labels[name]).grid(row=index, column=0, sticky="w", padx=(8, 6), pady=7)
            value = tk.StringVar(value=str(self.existing.get(name) or ""))
            entry = ttk.Entry(frame, textvariable=value, width=42)
            entry.grid(row=index, column=1, sticky="ew", padx=6, pady=7)
            self.values[name] = value
            if first is None:
                first = entry
        ttk.Label(frame, text="节次示例：1:1-2;3:5-6（周一第1–2节，周三第5–6节）。\n同组仅表示候选备选关系；冲突检查只是人工录入的初筛。",
                  foreground="#666", wraplength=370).grid(row=len(COURSE_FIELDS)+2, column=0,
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
        self.geometry("900x630")
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
        self.max_duration_minutes = tk.StringVar(value=str(strategy.get("max_duration_minutes", 480)))
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
        ttk.Label(settings, text="学年学期").grid(row=0, column=0, sticky="w", pady=5)
        ttk.Entry(settings, textvariable=self.semester, width=23).grid(
            row=0, column=1, sticky="ew", padx=6, pady=5)
        ttk.Label(settings, text="选课计划时间（北京时间）").grid(row=0, column=2, sticky="w", pady=5)
        ttk.Entry(settings, textvariable=self.target_time, width=23).grid(
            row=0, column=3, sticky="ew", padx=6, pady=5)
        ttk.Label(settings, text="当前主程序仅支持只读监测；定时打开官网是手动备用方案，不会自动提交。",
                  foreground="#666", wraplength=720).grid(
                      row=1, column=0, columnspan=4, sticky="w", pady=(4, 2))

        self.advanced_button = ttk.Button(
            outer, text="展开只读监测高级设置", command=self.toggle_advanced_settings)
        self.advanced_button.pack(anchor="w", pady=(7, 2))
        self.advanced_settings = ttk.LabelFrame(
            outer, text="只读监测高级设置（不提供自动提交）", padding=10)
        advanced = self.advanced_settings
        for col in range(4):
            advanced.columnconfigure(col, weight=1 if col % 2 else 0)
        ttk.Label(advanced, text="查询模式").grid(row=0, column=0, sticky="w", pady=5)
        ttk.Combobox(advanced, textvariable=self.mode,
                     values=("monitor", "burst"), state="readonly", width=22).grid(
                         row=0, column=1, sticky="ew", padx=6, pady=5)
        ttk.Label(advanced, text="monitor=只读监测；burst=只在指定时间读取一次，不是抢课。",
                  foreground="#666").grid(row=0, column=2, columnspan=2, sticky="w", padx=6)
        ttk.Label(advanced, text="只读查询间隔方案").grid(row=1, column=0, sticky="w", pady=5)
        presets = ttk.Combobox(advanced, textvariable=self.interval_preset,
                               values=(*INTERVAL_PRESETS, INTERVAL_CUSTOM),
                               state="readonly", width=22)
        presets.grid(row=1, column=1, sticky="ew", padx=6, pady=5)
        presets.bind("<<ComboboxSelected>>", self._apply_interval_preset)
        for label, var, row, col in (
            ("间隔下限（秒）", self.interval_low, 2, 0),
            ("间隔上限（秒）", self.interval_high, 2, 2),
            ("最多查询次数（0=按时长）", self.max_cycles, 3, 0),
            ("最长监测分钟", self.max_duration_minutes, 3, 2),
        ):
            ttk.Label(advanced, text=label).grid(row=row, column=col, sticky="w", pady=5)
            ttk.Entry(advanced, textvariable=var, width=23).grid(
                row=row, column=col + 1, sticky="ew", padx=6, pady=5)
        ttk.Label(advanced, text="以上仅用于只读查询。实际频率还必须满足学校规则；设置速度不会启用抢课。",
                  foreground="#666", wraplength=720).grid(
                      row=4, column=0, columnspan=4, sticky="w", pady=(6, 2))

        wrapper = ttk.LabelFrame(outer, text="目标课程（离线草稿允许 0–30 门）", padding=8)
        wrapper.pack(fill="both", expand=True, pady=(12, 6))
        self.table = ttk.Treeview(
            wrapper,
            columns=("priority", "key", "code", "class", "group", "name", "teacher"),
            show="headings", height=8,
        )
        for name, label, width in (("priority", "顺序", 55), ("key", "志愿", 70),
                                    ("code", "课程代码", 125), ("class", "教学班", 95),
                                    ("group", "备选组", 90), ("name", "课程名称", 170),
                                    ("teacher", "教师", 95)):
            self.table.heading(name, text=label)
            self.table.column(name, width=width, stretch=True)
        bar = ttk.Scrollbar(wrapper, orient="vertical", command=self.table.yview)
        self.table.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.table.pack(fill="both", expand=True)
        ttk.Label(
            wrapper,
            text="优先级按表格从上到下；同一非空备选组只需成功选择其中一门。保存方案会保留此顺序。",
            foreground="#666", wraplength=800,
        ).pack(anchor="w", pady=(5, 0))
        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(0, 10))
        ttk.Button(buttons, text="新增课程", command=self.add_course).pack(side="left", padx=(0, 7))
        ttk.Button(buttons, text="编辑所选", command=self.edit_course).pack(side="left", padx=(0, 7))
        ttk.Button(buttons, text="删除所选", command=self.remove_course).pack(side="left")
        ttk.Button(buttons, text="↑ 上移", command=lambda: self.move_course(-1)).pack(side="left", padx=(10, 4))
        ttk.Button(buttons, text="↓ 下移", command=lambda: self.move_course(1)).pack(side="left", padx=4)
        ttk.Button(buttons, text="检查节次冲突", command=self.check_conflicts).pack(side="left", padx=10)
        ttk.Button(buttons, text="取消", command=self.destroy).pack(side="right")
        ttk.Button(buttons, text="保存到配置文件", command=self.save).pack(side="right", padx=8)
        ttk.Label(outer, text="保存时会清除 YAML 中的 Cookie 和推送令牌。若需使用，应通过环境变量提供。",
                  foreground="#666", wraplength=730).pack(anchor="w")
        self.table.bind("<Double-1>", lambda _: self.edit_course())

    def toggle_advanced_settings(self):
        if self.advanced_settings.winfo_manager():
            self.advanced_settings.pack_forget()
            self.advanced_button.configure(text="展开只读监测高级设置")
        else:
            self.advanced_settings.pack(fill="x", after=self.advanced_button)
            self.advanced_button.configure(text="收起只读监测高级设置")

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
                i + 1, course.get("key", ""), course.get("course_id", ""),
                course.get("class_id", ""), course.get("group", ""),
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
            self.courses.pop(i)
            self.refresh_table()
            if self.courses:
                selected = min(i, len(self.courses) - 1)
                self.table.selection_set(str(selected))
                self.table.focus(str(selected))

    def move_course(self, offset: int):
        """Move the selected course in the persisted top-to-bottom priority order."""
        if offset not in (-1, 1):
            raise ValueError("志愿移动方向只能是上移或下移")
        index = self.selection()
        if index is None:
            messagebox.showinfo("请选择课程", "先选择要调整优先级的课程。", parent=self)
            return
        destination = index + offset
        if destination < 0 or destination >= len(self.courses):
            self.bell()
            return
        self.courses[index], self.courses[destination] = (
            self.courses[destination], self.courses[index]
        )
        self.refresh_table()
        self.table.selection_set(str(destination))
        self.table.focus(str(destination))
        self.table.see(str(destination))

    def check_conflicts(self):
        try:
            collisions, missing = find_conflicts(self.courses)
        except ValueError as exc:
            messagebox.showerror("节次录入错误", str(exc), parent=self)
            return
        lines = []
        if collisions:
            for clash in collisions[:10]:
                lines.append(f"{clash.first_key} 与 {clash.second_key}：周{clash.day}第{clash.first_period}-{clash.last_period}节")
            if len(collisions) > 10:
                lines.append(f"另有 {len(collisions) - 10} 项未展示")
        else:
            lines.append("已录入的不同备选组课程之间未发现节次重叠。")
        if missing:
            lines.append("未录入节次（未检查）：" + "、".join(missing[:15]))
        lines.append("仅依据本机手动填写的节次，不涵盖单双周、调课和学校规则。")
        messagebox.showinfo("本地课程冲突初筛", "\n".join(lines), parent=self)

    def save(self):
        try:
            interval_low, interval_high = parse_interval_range(
                self.interval_low.get(), self.interval_high.get())
            revised = update_config(
                self.config_data, courses=self.courses, semester=self.semester.get(),
                mode=self.mode.get(), target_time=self.target_time.get(),
                interval_low=interval_low, interval_high=interval_high,
                max_cycles=int(self.max_cycles.get()),
                max_duration_minutes=int(self.max_duration_minutes.get()))
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
