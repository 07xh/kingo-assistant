"""Compact LightC-style visual shell for the existing Tk application.

No network, configuration, background work, or course state lives here.
The original ttk.Notebook and its three tabs remain the navigation source of
truth; sidebar highlights only mirror the currently selected tab.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

PALETTE = {
    "background": "#F3F6FB",
    "surface": "#FFFFFF",
    "surface_alt": "#F0F4FA",
    "ink": "#17233A",
    "muted": "#64748B",
    "stroke": "#DFE7F0",
    "sidebar": "#151D32",
    "sidebar_hover": "#25314D",
    "sidebar_muted": "#B5C2D8",
    "accent": "#4869E8",
    "accent_hover": "#3859D6",
    "selected": "#DDE7FF",
}

NAV_SECTIONS = (
    ("home", "⌂", "首页", "tab_home"),
    ("manual", "⇄", "接口", "tab_manual"),
    ("tools", "☷", "工具", "tab_tools"),
)


def apply_light_theme(owner) -> None:
    """Configure one stable light theme without importing third-party UI kits."""
    p = PALETTE
    owner.configure(bg=p["background"])
    style = ttk.Style(owner)
    if "clam" in style.theme_names():
        style.theme_use("clam")
    style.configure(
        "TFrame", background=p["surface"])
    style.configure(
        "TLabel", background=p["surface"], foreground=p["ink"],
        font=("Microsoft YaHei UI", 9))
    style.configure(
        "TLabelframe", background=p["surface"],
        bordercolor=p["stroke"], lightcolor=p["stroke"],
        darkcolor=p["stroke"], borderwidth=1, relief="solid")
    style.configure(
        "TLabelframe.Label", background=p["surface"], foreground=p["ink"],
        font=("Microsoft YaHei UI", 10, "bold"))
    style.configure(
        "KingoStep.TLabelframe", background=p["surface"],
        bordercolor=p["stroke"], borderwidth=1, relief="solid")
    style.configure(
        "KingoStep.TLabelframe.Label", background=p["surface"],
        foreground=p["ink"], font=("Microsoft YaHei UI", 10, "bold"))
    style.configure(
        "KingoTitle.TLabel", background=p["surface"],
        foreground=p["ink"], font=("Microsoft YaHei UI", 18, "bold"))
    style.configure(
        "KingoSubtitle.TLabel", background=p["surface"],
        foreground=p["muted"], font=("Microsoft YaHei UI", 9))
    style.configure(
        "KingoStatus.TLabel", background=p["surface_alt"],
        foreground=p["muted"], font=("Microsoft YaHei UI", 9))
    style.configure(
        "TButton", padding=(12, 8),
        font=("Microsoft YaHei UI", 9), background=p["surface_alt"],
        foreground=p["ink"], bordercolor=p["stroke"], relief="flat",
        borderwidth=1, focuscolor=p["surface_alt"])
    style.map(
        "TButton", background=[
            ("pressed", p["selected"]), ("active", p["selected"])])
    style.configure(
        "KingoPrimary.TButton", padding=(14, 9),
        font=("Microsoft YaHei UI", 9, "bold"),
        background=p["accent"], foreground=p["surface"],
        bordercolor=p["accent"], borderwidth=1, relief="flat")
    style.map(
        "KingoPrimary.TButton",
        background=[("disabled", p["stroke"]),
                    ("pressed", p["accent_hover"]),
                    ("active", p["accent_hover"])],
        foreground=[("disabled", p["muted"])])
    style.configure(
        "KingoQuiet.TButton", padding=(10, 7),
        background=p["surface"], foreground=p["muted"],
        bordercolor=p["stroke"])
    style.configure(
        "Treeview", rowheight=32, background=p["surface"],
        fieldbackground=p["surface"], foreground=p["ink"],
        borderwidth=0, font=("Microsoft YaHei UI", 9))
    style.map(
        "Treeview", background=[("selected", p["selected"])],
        foreground=[("selected", p["ink"])])
    style.configure(
        "Treeview.Heading", background=p["surface_alt"],
        foreground=p["muted"], padding=(8, 8),
        font=("Microsoft YaHei UI", 9, "bold"),
        relief="flat", borderwidth=0)
    style.configure(
        "Kingo.Content.TNotebook", background=p["surface"],
        borderwidth=0, tabmargins=0, padding=0)
    # Tabs remain live and programmatically selectable, but the sidebar is
    # their sole visual navigation. This keeps old Notebook-based callbacks.
    style.layout("Kingo.Content.TNotebook.Tab", [])


def make_visual_shell(owner) -> ttk.Frame:
    """Install a narrow dark rail and the light content surface."""
    p = PALETTE
    shell = tk.Frame(owner, bg=p["background"])
    shell.pack(fill="both", expand=True)
    owner.visual_shell = shell

    sidebar = tk.Frame(shell, bg=p["sidebar"], width=98)
    sidebar.pack(side="left", fill="y", padx=(10, 0), pady=10)
    sidebar.pack_propagate(False)
    owner.sidebar = sidebar

    brand = tk.Frame(sidebar, bg=p["sidebar"])
    brand.pack(fill="x", pady=(19, 21))
    tk.Label(
        brand, text="K", bg=p["accent"], fg=p["surface"],
        font=("Segoe UI", 18, "bold"), width=3, height=2,
    ).pack()
    tk.Label(
        brand, text="KINGO", bg=p["sidebar"], fg=p["surface"],
        font=("Segoe UI", 9, "bold"),
    ).pack(pady=(9, 0))
    tk.Label(
        brand, text="ASSISTANT", bg=p["sidebar"], fg=p["sidebar_muted"],
        font=("Segoe UI", 7),
    ).pack(pady=(2, 0))

    owner.sidebar_buttons = {}
    for key, glyph, title, target in NAV_SECTIONS:
        button = tk.Button(
            sidebar,
            text=f"{glyph}\n{title}", justify="center",
            font=("Microsoft YaHei UI", 10),
            bg=p["sidebar"], fg=p["sidebar_muted"],
            activebackground=p["sidebar_hover"],
            activeforeground=p["surface"], borderwidth=0,
            relief="flat", highlightthickness=0, cursor="hand2",
            padx=4, pady=10, takefocus=True,
            command=lambda attr=target: owner.notebook.select(
                getattr(owner, attr)),
        )
        button.pack(fill="x", padx=9, pady=(0, 7))
        button.bind(
            "<Return>", lambda event, control=button: control.invoke())
        owner.sidebar_buttons[key] = button

    tk.Label(
        sidebar, text="只读 · 安全",
        bg=p["sidebar"], fg=p["sidebar_muted"],
        font=("Microsoft YaHei UI", 8),
    ).pack(side="bottom", pady=(0, 17))
    content = ttk.Frame(shell)
    content.pack(side="left", fill="both", expand=True,
                 padx=(10, 10), pady=10)
    owner.visual_content = content
    return content


def sync_sidebar(owner, _event=None) -> None:
    """Mirror the real Notebook selection, including programmatic navigation."""
    if not getattr(owner, "sidebar_buttons", None):
        return
    selected = str(owner.notebook.select())
    p = PALETTE
    for key, _glyph, _title, target in NAV_SECTIONS:
        button = owner.sidebar_buttons[key]
        active = selected == str(getattr(owner, target))
        button.configure(
            bg=p["accent"] if active else p["sidebar"],
            fg=p["surface"] if active else p["sidebar_muted"],
            activebackground=p["accent_hover"] if active else p["sidebar_hover"],
        )


def connect_sidebar(owner) -> None:
    """Initial selection and future tab changes share one source of truth."""
    owner.notebook.bind(
        "<<NotebookTabChanged>>", lambda event: sync_sidebar(owner), add="+")
    sync_sidebar(owner)


def make_scrollable_content(parent):
    """Return a full-width scrollable content frame for dense Home/Tools pages.

    The Notebook page remains the real navigation target, with a canvas
    hosting only the view widgets. No school state or callbacks are moved.
    """
    p = PALETTE
    viewport = ttk.Frame(parent)
    viewport.pack(fill="both", expand=True)
    scrollbar = ttk.Scrollbar(viewport, orient="vertical")
    canvas = tk.Canvas(
        viewport, bg=p["surface"], borderwidth=0,
        highlightthickness=0, relief="flat",
        yscrollcommand=scrollbar.set,
    )
    scrollbar.configure(command=canvas.yview)
    scrollbar.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)
    content = ttk.Frame(canvas)
    window_id = canvas.create_window((0, 0), window=content, anchor="nw")

    def update_scrollregion(_event=None):
        canvas.configure(scrollregion=canvas.bbox("all"))
        available = canvas.winfo_height()
        need_scroll = content.winfo_reqheight() > available + 2
        if need_scroll and not scrollbar.winfo_manager():
            scrollbar.pack(side="right", fill="y")
        elif not need_scroll and scrollbar.winfo_manager():
            scrollbar.pack_forget()

    def match_viewport(event):
        canvas.itemconfigure(window_id, width=max(1, event.width))
        update_scrollregion()

    content.bind("<Configure>", update_scrollregion, add="+")
    canvas.bind("<Configure>", match_viewport, add="+")
    return content, canvas, scrollbar


def bind_page_wheel(canvas, content) -> None:
    """Scroll the outer page on passive controls; preserve Treeview/Text wheels."""
    def on_wheel(event):
        if getattr(event, "num", None) == 4:
            canvas.yview_scroll(-3, "units")
        elif getattr(event, "num", None) == 5:
            canvas.yview_scroll(3, "units")
        elif getattr(event, "delta", 0):
            delta = event.delta
            steps = max(1, int(abs(delta) / 120))
            canvas.yview_scroll(-steps if delta > 0 else steps, "units")

    def attach(widget):
        # Widgets with their own scrolling must keep that behavior.
        if widget.winfo_class() not in (
            "Treeview", "Text", "TScrollbar", "Scrollbar",
            "TEntry", "Entry", "Spinbox", "TSpinbox",
            "Listbox", "Canvas",
        ):
            for event_name in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                widget.bind(event_name, on_wheel, add="+")
        for child in widget.winfo_children():
            attach(child)

    attach(content)


def update_responsive_shell(owner, _event=None) -> None:
    """Adapt the visible chrome to narrower Windows without affecting state."""
    if _event is not None and _event.widget is not owner:
        return
    width = owner.winfo_width()
    height = owner.winfo_height()
    compact = width < 1120
    sidebar_width = 80 if compact else 98
    owner.sidebar.configure(width=sidebar_width)

    desired = "compact" if compact else "regular"
    if getattr(owner, "_shell_size_mode", None) != desired:
        owner._shell_size_mode = desired
        owner.shell_subtitle.configure(
            text=(
                "S202 只读已接入 · 待账号验收  |  自动提交不可用"
                if compact else
                "原生登录与 S202 只读查询已实现 · 待本人账号验收  |  自动提交未接通"
            ),
        )

    # Keep the compact status footer inside the remaining content width.
    usable = max(360, width - sidebar_width - 100)
    owner.status_bar.configure(wraplength=usable)

    # A low-height desktop should spend its space on actions, not boilerplate.
    short = height < 660
    if short and owner.shell_subtitle.winfo_manager():
        owner.shell_subtitle.pack_forget()
    elif not short and not owner.shell_subtitle.winfo_manager():
        owner.shell_subtitle.pack(anchor="w", pady=(6, 0))

    # The original ttk Labels used a fixed 920/670px wraplength; at small
    # widths this could clip current status messages instead of wrapping.
    for root in (owner.tab_home, owner.tab_manual, owner.tab_tools):
        pending = [root]
        while pending:
            widget = pending.pop()
            if isinstance(widget, ttk.Label):
                try:
                    current = int(widget.cget("wraplength"))
                except (ValueError, tk.TclError):
                    current = 0
                original = getattr(widget, "_kingo_original_wrap", None)
                if original is None:
                    original = current
                    widget._kingo_original_wrap = original
                if original >= 600:
                    desired_wrap = min(original, usable)
                    if current != desired_wrap:
                        widget.configure(wraplength=desired_wrap)
            pending.extend(widget.winfo_children())


def bind_responsive_shell(owner) -> None:
    owner.bind(
        "<Configure>", lambda event: update_responsive_shell(owner, event),
        add="+")
    update_responsive_shell(owner)
