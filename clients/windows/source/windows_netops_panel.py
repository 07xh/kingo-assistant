"""Network / clock / session panel for the Tools page.

Owns three read-only measurements that the rest of the app can reuse:

* candidate-node sweep (TCP+TLS handshake only, no business request)
* server clock offset (kept in memory, never written to the system clock)
* session keepalive with conservative relogin

Threading contract: every socket call happens in a daemon worker thread and
only publishes results through ``queue.Queue``. Widgets are touched solely by
``_pump`` on the Tk main thread. Nothing starts automatically at build time.
"""
from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import ttk

from core.netops import clock_offset, node_race, session_guard
from core.netops import panel_model


class NetOpsPanel:
    """Build the panel and keep its worker lifecycle in one place."""

    def __init__(self, app, parent, default_url=""):
        self.app = app
        self.widget = parent
        self.default_url = default_url or ""
        self.queue = queue.Queue()
        self.ranked = []
        self.offset = None
        self.guard = None
        self._race_stop = threading.Event()
        self._closed = False
        self._pump_job = None
        self._build(parent)
        self._pump()

    # ------------------------------------------------------------------ UI
    def _build(self, parent):
        card = ttk.Frame(parent, style="KingoCard.TFrame", padding=(16, 14))
        card.pack(fill="x", padx=10, pady=(2, 8))
        self.card = card

        head = ttk.Frame(card, style="KingoCard.TFrame")
        head.pack(fill="x")
        ttk.Label(
            head, text="网络与时间（本机实测 · 只读）",
            style="KingoCardTitle.TLabel",
        ).pack(side="left")
        self.badge_var = tk.StringVar(value="时钟未校准")
        self.badge = ttk.Label(
            head, textvariable=self.badge_var, style="KingoBadgeMuted.TLabel")
        self.badge.pack(side="right")
        ttk.Label(
            card,
            text="测速只做 TCP+TLS 握手，不查询课程；时间校准只读响应头，"
                 "不修改系统时钟。两者都不会提交任何选课请求。",
            style="KingoHint.TLabel", wraplength=820,
        ).pack(anchor="w", pady=(4, 10))

        self._build_race(card)
        self._build_clock(card)
        self._build_guard(card)

    def _build_race(self, parent):
        block = ttk.Frame(parent, style="KingoCard.TFrame")
        block.pack(fill="x", pady=(0, 10))
        row = ttk.Frame(block, style="KingoCard.TFrame")
        row.pack(fill="x")
        ttk.Label(row, text="① 节点测速", style="KingoSection.TLabel").pack(side="left")
        self.race_button = ttk.Button(
            row, text="开始测速", style="KingoPrimary.TButton",
            command=self.start_race)
        self.race_button.pack(side="right")
        self.race_stop_button = ttk.Button(
            row, text="停止", style="KingoQuiet.TButton",
            command=self.stop_race, state="disabled")
        self.race_stop_button.pack(side="right", padx=(0, 8))

        self.hosts_text = tk.Text(
            block, height=3, wrap="none", relief="flat",
            bg="#FFFFFF", fg="#17233A", insertbackground="#17233A",
            highlightthickness=1, highlightbackground="#DFE7F0",
            highlightcolor="#4869E8", font=("Consolas", 9), padx=8, pady=6,
        )
        self.hosts_text.pack(fill="x", pady=(6, 0))
        self.hosts_text.insert(
            "1.0", "# 每行一个候选主机或 IP，可用 host 备注 的形式；测速前请自行确认目标\n")

        table_wrap = ttk.Frame(block, style="KingoCard.TFrame")
        table_wrap.pack(fill="x", pady=(8, 0))
        self.race_table = ttk.Treeview(
            table_wrap, columns=("host", "median", "ok", "fail"),
            show="headings", height=4, style="Kingo.Treeview")
        for column, label, width, anchor in (
            ("host", "主机", 240, "w"),
            ("median", "中位握手", 110, "e"),
            ("ok", "成功", 70, "e"),
            ("fail", "失败", 70, "e"),
        ):
            self.race_table.heading(column, text=label)
            self.race_table.column(column, width=width, anchor=anchor,
                                   stretch=(column == "host"))
        self.race_table.pack(side="left", fill="x", expand=True)
        ttk.Scrollbar(
            table_wrap, orient="vertical", command=self.race_table.yview,
        ).pack(side="right", fill="y")

        self.race_status = tk.StringVar(
            value="尚未测速。粘贴候选主机后点击「开始测速」。")
        ttk.Label(block, textvariable=self.race_status,
                  style="KingoHint.TLabel", wraplength=820).pack(
            anchor="w", pady=(6, 0))

    def _build_clock(self, parent):
        block = ttk.Frame(parent, style="KingoCard.TFrame")
        block.pack(fill="x", pady=(0, 10))
        row = ttk.Frame(block, style="KingoCard.TFrame")
        row.pack(fill="x")
        ttk.Label(row, text="② 服务器时钟校准", style="KingoSection.TLabel").pack(side="left")
        self.clock_button = ttk.Button(
            row, text="校准", style="KingoQuiet.TButton", command=self.start_clock)
        self.clock_button.pack(side="right")

        url_row = ttk.Frame(block, style="KingoCard.TFrame")
        url_row.pack(fill="x", pady=(6, 0))
        ttk.Label(url_row, text="校准地址", style="KingoHint.TLabel").pack(side="left")
        self.url_var = tk.StringVar(value=self.default_url)
        self.url_entry = ttk.Entry(url_row, textvariable=self.url_var, width=52)
        self.url_entry.pack(side="left", fill="x", expand=True, padx=(8, 0))

        self.clock_status = tk.StringVar(
            value="未校准。窗口判定仍使用本机时间，跨机器可能差数百毫秒。")
        ttk.Label(block, textvariable=self.clock_status,
                  style="KingoHint.TLabel", wraplength=820).pack(
            anchor="w", pady=(6, 0))

    def _build_guard(self, parent):
        block = ttk.Frame(parent, style="KingoCard.TFrame")
        block.pack(fill="x")
        row = ttk.Frame(block, style="KingoCard.TFrame")
        row.pack(fill="x")
        ttk.Label(row, text="③ 会话守护", style="KingoSection.TLabel").pack(side="left")
        self.guard_button = ttk.Button(
            row, text="启动守护", style="KingoQuiet.TButton",
            command=self.toggle_guard)
        self.guard_button.pack(side="right")
        ttk.Label(row, text="间隔(秒)", style="KingoHint.TLabel").pack(
            side="right", padx=(10, 6))
        self.interval_var = tk.StringVar(value="60")
        ttk.Spinbox(
            row, from_=15, to=3600, increment=15, width=6,
            textvariable=self.interval_var,
        ).pack(side="right")

        self.guard_status = tk.StringVar(
            value="守护已停止。只有明确识别为「登录失效」才会重登，"
                  "限流与超时只退避。")
        ttk.Label(block, textvariable=self.guard_status,
                  style="KingoHint.TLabel", wraplength=820).pack(
            anchor="w", pady=(6, 0))

    # ------------------------------------------------------- measurement API
    def start_race(self):
        if self.queue.qsize() > 64:
            return
        hosts = panel_model.parse_hosts(self.hosts_text.get("1.0", "end"))
        if not hosts:
            self.race_status.set("没有可测的主机：每行一个域名或 IP，不能只有注释。")
            return
        self.ranked = []
        self._race_stop.clear()
        for item in self.race_table.get_children():
            self.race_table.delete(item)
        self.race_button.configure(state="disabled")
        self.race_stop_button.configure(state="normal")
        self.race_status.set("正在测速 %d 个候选（每轮 TCP+TLS 握手，共 3 轮）…" % len(hosts))

        def worker():
            try:
                ranked = node_race.rank_hosts(
                    hosts, rounds=3, workers=min(32, max(4, len(hosts))),
                    should_stop=self._race_stop.is_set)
            except Exception as exc:                 # surfaced, never silent
                self.queue.put(("race_error", repr(exc)))
                return
            self.queue.put(("race_done", ranked, len(hosts)))

        threading.Thread(target=worker, name="kingo-node-race", daemon=True).start()

    def stop_race(self):
        self._race_stop.set()
        self.race_status.set("已请求停止：本轮测速结束后返回已有结果。")

    def start_clock(self):
        url = (self.url_var.get() or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            self.clock_status.set("校准地址需要以 http:// 或 https:// 开头。")
            return
        self.clock_button.configure(state="disabled")
        self.clock_status.set("正在取样 %s …" % panel_model.mask_url(url))

        def worker():
            try:
                result = clock_offset.estimate(url, samples=7)
            except Exception as exc:
                self.queue.put(("clock_error", repr(exc)))
                return
            self.queue.put(("clock_done", result))

        threading.Thread(target=worker, name="kingo-clock", daemon=True).start()

    def toggle_guard(self):
        if self.guard and self.guard.running:
            self.guard.stop()
            self.guard_button.configure(text="启动守护")
            self.guard_status.set(panel_model.guard_text(self.guard.stats, False))
            return
        url = (self.url_var.get() or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            self.guard_status.set("守护需要一个可探活的 http(s) 地址，已复用上方校准地址。")
            return
        try:
            interval = float(self.interval_var.get())
        except ValueError:
            interval = 60.0
        interval = max(15.0, min(3600.0, interval))

        def probe():
            return session_guard.probe_url_headers(url, timeout=5.0)

        def relogin():
            # No write adapter ships with this client: report failure honestly
            # instead of pretending a relogin happened.
            self.queue.put(("guard_event", "relogin_fail",
                            "未接入落实验收的登录协议，无法自动重登"))
            return False

        self.guard = session_guard.SessionGuard(
            probe, relogin, interval=interval,
            on_event=lambda kind, message: self.queue.put(
                ("guard_event", kind, message)),
        ).start()
        self.guard_button.configure(text="停止守护")
        self.guard_status.set("守护已启动 · 间隔 %d 秒 · 只做只读 HEAD 探活" % int(interval))

    def get_offset(self):
        """Offset in seconds for callers that judge server-side windows."""
        return self.offset

    # --------------------------------------------------------- main-thread pump
    def _pump(self):
        if self._closed:
            return
        dirty_guard = False
        while True:
            try:
                item = self.queue.get_nowait()
            except queue.Empty:
                break
            kind = item[0]
            if kind == "race_done":
                self._apply_race(item[1], item[2])
            elif kind == "race_error":
                self.race_status.set("测速失败：%s" % item[1])
                self._reset_race_buttons()
            elif kind == "clock_done":
                self._apply_clock(item[1])
            elif kind == "clock_error":
                self.clock_status.set("校准失败：%s" % item[1])
                self.clock_button.configure(state="normal")
            elif kind == "guard_event":
                dirty_guard = True
                if item[1] == "relogin_fail":
                    self.guard_status.set("守护：%s" % item[2])
        if self.guard and dirty_guard:
            self.guard_status.set(panel_model.guard_text(
                self.guard.stats, self.guard.running))
        self._pump_job = self.widget.after(200, self._pump)

    def _reset_race_buttons(self):
        self.race_button.configure(state="normal")
        self.race_stop_button.configure(state="disabled")

    def _apply_race(self, ranked, candidates):
        self.ranked = ranked
        for item in self.race_table.get_children():
            self.race_table.delete(item)
        for row in ranked:
            self.race_table.insert("", "end", values=panel_model.format_race_row(row))
        self.race_status.set(panel_model.summarize_race(ranked, candidates))
        self._reset_race_buttons()

    def _apply_clock(self, result):
        self.offset = result.get("offset")
        self.clock_status.set(panel_model.offset_text(result))
        offset_ms = None if self.offset is None else self.offset * 1000.0
        tone = panel_model.offset_tone(offset_ms)
        self.badge.configure(style="KingoBadge%s.TLabel" % tone.capitalize())
        self.badge_var.set(panel_model.offset_badge_text(offset_ms))
        self.clock_button.configure(state="normal")
        if self.offset is not None:
            self.app.log("时钟校准完成：%s" % panel_model.offset_text(result))

    def close(self):
        self._closed = True
        self._race_stop.set()
        if self.guard:
            self.guard.stop(timeout=1.0)
        if self._pump_job is not None:
            try:
                self.widget.after_cancel(self._pump_job)
            except tk.TclError:
                pass
            self._pump_job = None


def build_netops_panel(app, parent, default_url=""):
    """Create the panel once, expose it on the application for later reuse."""
    panel = NetOpsPanel(app, parent, default_url=default_url)
    app.netops = panel
    return panel
