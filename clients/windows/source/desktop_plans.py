"""Tk front-end for safe local course-plan snapshots."""
from __future__ import annotations

from pathlib import Path
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk

from core.desktop_settings import load_config, save_config_atomic
from core.plan_store import delete_plan, list_plans, load_plan, save_plan, save_switch_backup


class PlanManager(tk.Toplevel):
    def __init__(self, parent: tk.Misc, *, config_path: Path, plans_dir: Path, on_saved):
        super().__init__(parent)
        self.title("课程方案管理（本地，不联网）")
        self.geometry("600x420")
        self.minsize(510, 350)
        self.transient(parent)
        self.config_path = config_path
        self.plans_dir = plans_dir
        self.on_saved = on_saved
        outer = ttk.Frame(self, padding=18)
        outer.pack(fill="both", expand=True)
        ttk.Label(outer, text="课程方案", font=("Microsoft YaHei UI", 14, "bold")).pack(anchor="w")
        ttk.Label(outer, text="保存当前学期、课程志愿和低频检查设置；不保存 Cookie、登录会话或学校接口。",
                  wraplength=550, foreground="#666").pack(anchor="w", pady=(5, 12))
        frame = ttk.LabelFrame(outer, text="已保存的方案", padding=8)
        frame.pack(fill="both", expand=True)
        self.listbox = tk.Listbox(frame, exportselection=False, height=8)
        self.listbox.pack(side="left", fill="both", expand=True)
        scroller = ttk.Scrollbar(frame, orient="vertical", command=self.listbox.yview)
        scroller.pack(side="right", fill="y")
        self.listbox.configure(yscrollcommand=scroller.set)
        buttons = ttk.Frame(outer)
        buttons.pack(fill="x", pady=(12, 0))
        ttk.Button(buttons, text="保存当前方案…", command=self.save_current).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="切换到所选", command=self.apply_selected).pack(side="left", padx=(0, 8))
        ttk.Button(buttons, text="删除所选", command=self.remove_selected).pack(side="left")
        ttk.Button(buttons, text="关闭", command=self.destroy).pack(side="right")
        self.refresh()
        self.grab_set()

    def refresh(self) -> None:
        self.listbox.delete(0, "end")
        for title in list_plans(self.plans_dir):
            self.listbox.insert("end", title)

    def selected(self) -> str | None:
        indexes = self.listbox.curselection()
        return str(self.listbox.get(indexes[0])) if indexes else None

    def save_current(self) -> None:
        name = simpledialog.askstring("保存课程方案", "输入方案名称（如：大一上·首选）：", parent=self)
        if name is None:
            return
        try:
            config = load_config(self.config_path)
            if name.strip() in list_plans(self.plans_dir):
                if not messagebox.askyesno("覆盖已有方案", "同名方案已存在，确定覆盖吗？", parent=self):
                    return
            save_plan(self.plans_dir, name, config)
            self.refresh()
            messagebox.showinfo("已保存", "方案仅保存在本机，没有传输至学校或云端。", parent=self)
        except (ValueError, KeyError, OSError) as exc:
            messagebox.showerror("保存失败", "方案校验失败：" + type(exc).__name__, parent=self)

    def apply_selected(self) -> None:
        title = self.selected()
        if not title:
            messagebox.showinfo("选择方案", "请先从列表选择一个课程方案。", parent=self)
            return
        if not messagebox.askyesno("确认切换", f"切换到『{title}』？当前配置将生成独立备份，不会覆盖之前的恢复点。", parent=self):
            return
        try:
            current = load_config(self.config_path)
            new_config = load_plan(self.plans_dir, title, current)
            backup_name = save_switch_backup(self.plans_dir, current)
            save_config_atomic(self.config_path, new_config)
            self.on_saved()
            self.refresh()
            messagebox.showinfo("切换完成", "本次切换完成；已保存恢复点：" + backup_name + "。\n请重新核对学期与教务开放状态。YAML 原有 Cookie 将按安全规则清除。", parent=self)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            messagebox.showerror("切换失败", "方案未通过结构校验：" + type(exc).__name__, parent=self)

    def remove_selected(self) -> None:
        title = self.selected()
        if not title:
            return
        if not messagebox.askyesno("确认删除", f"确定删除本机方案『{title}』？", parent=self):
            return
        try:
            delete_plan(self.plans_dir, title)
            self.refresh()
        except (ValueError, OSError) as exc:
            messagebox.showerror("删除失败", type(exc).__name__, parent=self)
