"""Typography, theme, and window layout for the Windows workbench."""
from __future__ import annotations

import sys
import tkinter as tk
from tkinter import font, ttk

PALETTE = {
    "background": "#F5F5F5",
    "surface": "#FFFFFF",
    "panel": "#F5F5F5",
    "panel_alt": "#EEEEEE",
    "ink": "#242424",
    "muted": "#5B5B5B",
    "stroke": "#D7D7D7",
    "stroke_soft": "#E3E3E3",
    "accent": "#005EAE",
    "accent_hover": "#004B8C",
    "accent_soft": "#F1F1F1",
    "selected": "#E4F0FA",
    "ok": "#0B6A0B",
    "warn": "#8A5300",
    "danger": "#A4262C",
    "grid_header": "#F5F5F5",
}

# Point sizes, not pixels: the desktop client this layout mirrors is a
# 9pt-at-96dpi Win32 application, and Tk applies the display scale factor
# itself. Fixed pixel sizes would look cramped on a scaled display.
FONT_UI = ("Microsoft YaHei UI", 9)
FONT_BOLD = ("Microsoft YaHei UI", 9, "bold")
FONT_SMALL = ("Microsoft YaHei UI", 8)

TABS = (
    ("tab_home", "选课工作台"),
    ("tab_tools", "教务与工具"),
    ("tab_history", "执行记录"),
    ("tab_account", "账号与登录"),
    ("tab_settings", "抢课设置"),
)

TOOLBAR_HEIGHT = 34
DEFAULT_SIZE = (1120, 710)
MINIMUM_SIZE = (940, 600)


def enable_windows_dpi() -> None:
    """Apply the system DPI before Tk creates the Windows HWND."""
    if sys.platform == "win32":
        from ctypes import windll

        windll.user32.SetProcessDPIAware()


def display_scale(widget) -> float:
    return widget.winfo_fpixels("1i") / 96


def size_window(owner) -> None:
    scale = display_scale(owner)
    screen = (owner.winfo_screenwidth() - round(24 * scale),
              owner.winfo_screenheight() - round(80 * scale))
    width, height = (min(round(size * scale), limit)
                     for size, limit in zip(DEFAULT_SIZE, screen))
    owner.geometry(f"{width}x{height}")
    owner.minsize(*(min(round(size * scale), limit)
                    for size, limit in zip(MINIMUM_SIZE, screen)))


def apply_light_theme(owner) -> None:
    """Install the dense desktop theme once; callers never re-run it."""
    p = PALETTE
    owner.configure(bg=p["background"])
    style = ttk.Style(owner)
    if "clam" in style.theme_names():
        style.theme_use("clam")
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont"):
        font.nametofont(name, root=owner).configure(family=FONT_UI[0], size=FONT_UI[1])

    # --- containers -------------------------------------------------
    style.configure("TFrame", background=p["background"])
    style.configure("Shell.TFrame", background=p["background"])

    # --- text -------------------------------------------------------
    style.configure(
        "TLabel", background=p["background"], foreground=p["ink"],
        font=FONT_UI)
    style.configure("Muted.TLabel", foreground=p["muted"], font=FONT_SMALL)
    style.configure(
        "KingoStatus.TLabel", background=p["panel_alt"],
        foreground=p["ink"], font=FONT_SMALL)
    style.configure(
        "StatusCell.TLabel", background=p["panel_alt"],
        foreground=p["muted"], font=FONT_SMALL)

    # --- group boxes (WinForms GroupBox) ----------------------------
    style.configure(
        "TLabelframe", background=p["background"], bordercolor=p["stroke"],
        lightcolor=p["stroke"], darkcolor=p["stroke"],
        borderwidth=1, relief="solid", padding=2)
    style.configure(
        "TLabelframe.Label", background=p["background"], foreground=p["ink"],
        font=FONT_BOLD, padding=(2, 0))
    # --- buttons ----------------------------------------------------
    style.configure(
        "TButton", padding=(10, 4), width=0, font=FONT_UI,
        background=p["surface"], foreground=p["ink"],
        bordercolor=p["stroke"], borderwidth=1, relief="solid",
        lightcolor=p["stroke"], darkcolor=p["stroke"],
        focuscolor=p["accent"], anchor="center")
    style.map(
        "TButton",
        background=[("disabled", p["panel"]), ("pressed", p["accent_soft"]),
                    ("active", p["accent_soft"])],
        foreground=[("disabled", p["muted"])],
        bordercolor=[("focus", p["accent"])])
    style.configure(
        "KingoPrimary.TButton", padding=(10, 5), font=FONT_BOLD,
        background=p["accent"], foreground=p["surface"],
        bordercolor=p["accent"], lightcolor=p["accent"], darkcolor=p["accent"],
        focuscolor=p["surface"], borderwidth=1, relief="solid")
    style.map(
        "KingoPrimary.TButton",
        background=[("disabled", p["stroke_soft"]),
                    ("pressed", p["accent_hover"]),
                    ("active", p["accent_hover"])],
        foreground=[("disabled", p["muted"])])
    style.configure(
        "KingoQuiet.TButton", padding=(8, 3), font=FONT_UI,
        background=p["panel"], foreground=p["muted"],
        bordercolor=p["panel"], borderwidth=1, relief="flat")
    style.map(
        "KingoQuiet.TButton",
        background=[("active", p["accent_soft"])])

    # --- inputs -----------------------------------------------------
    style.configure(
        "TEntry", fieldbackground=p["surface"], foreground=p["ink"],
        bordercolor=p["stroke"], lightcolor=p["stroke"],
        darkcolor=p["stroke"], borderwidth=1, padding=(4, 3),
        insertcolor=p["ink"])
    style.configure(
        "TCombobox", fieldbackground=p["surface"], background=p["panel_alt"],
        foreground=p["ink"], arrowcolor=p["ink"], padding=(4, 2),
        bordercolor=p["stroke"], borderwidth=1)
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", p["surface"])],
        selectbackground=[("readonly", p["surface"])],
        selectforeground=[("readonly", p["ink"])])
    style.configure(
        "TSpinbox", fieldbackground=p["surface"], foreground=p["ink"],
        arrowcolor=p["ink"], bordercolor=p["stroke"], borderwidth=1,
        padding=(3, 2))
    style.configure(
        "TCheckbutton", background=p["background"], foreground=p["ink"],
        font=FONT_UI, focuscolor=p["background"], padding=(0, 2))
    style.map("TCheckbutton", background=[("active", p["background"])])
    style.configure(
        "TRadiobutton", background=p["background"], foreground=p["ink"],
        font=FONT_UI, focuscolor=p["background"])
    style.map("TRadiobutton", background=[("active", p["background"])])
    style.configure(
        "TScrollbar", background=p["panel_alt"], troughcolor=p["background"],
        bordercolor=p["stroke_soft"], arrowcolor=p["muted"],
        borderwidth=1, arrowsize=12)

    # --- data grid --------------------------------------------------
    style.configure(
        "Treeview", rowheight=round(23 * display_scale(owner)), background=p["surface"],
        fieldbackground=p["surface"], foreground=p["ink"],
        borderwidth=1, relief="solid", font=FONT_UI)
    style.map(
        "Treeview",
        background=[("selected", p["selected"])],
        foreground=[("selected", p["ink"])])
    style.configure(
        "Treeview.Heading", background=p["grid_header"],
        foreground=p["ink"], padding=(6, 4), font=FONT_BOLD,
        lightcolor=p["grid_header"], darkcolor=p["grid_header"],
        relief="flat", borderwidth=1)
    style.map(
        "Treeview.Heading", background=[("active", p["accent_soft"])])

    # --- tab control ------------------------------------------------
    # The strip stays visible: this build navigates with real Win32-style
    # tabs, so no ``style.layout(..., [])`` may hide the notebook again.
    style.configure(
        "Kingo.Content.TNotebook", background=p["background"],
        borderwidth=1, tabmargins=0, padding=0)
    style.configure(
        "Kingo.Content.TNotebook.Tab", background=p["panel_alt"],
        foreground=p["ink"], font=FONT_UI, padding=(14, 5),
        lightcolor="#FFFFFF", darkcolor=p["stroke_soft"],
        bordercolor=p["stroke"], borderwidth=1)
    style.map(
        "Kingo.Content.TNotebook.Tab",
        background=[("selected", p["surface"]),
                    ("active", p["accent_soft"])],
        lightcolor=[("selected", "#FFFFFF")],
        foreground=[("selected", p["accent"])],
        expand=[("selected", (0, 0, 0, 0))])


def make_visual_shell(owner) -> ttk.Frame:
    """Build command strip + status bar and return the page host frame."""
    p = PALETTE
    shell = tk.Frame(owner, bg=p["background"])
    shell.pack(fill="both", expand=True)
    owner.visual_shell = shell

    strip = tk.Frame(shell, bg=p["panel"], height=round(TOOLBAR_HEIGHT * display_scale(owner)))
    strip.pack(side="top", fill="x")
    strip.pack_propagate(False)
    owner.toolbar = strip

    brand = tk.Label(
        strip, text="华水选课助手", bg=p["panel"], fg=p["ink"],
        font=("Microsoft YaHei UI", 11, "bold"))
    brand.pack(side="left", padx=(9, 10))
    # Keep the help shortcut visible at the minimum width.
    owner.toolbar_extras = tk.Frame(strip, bg=p["panel"])
    owner.toolbar_extras.pack(side="right", padx=(0, 6))

    tk.Frame(shell, bg=p["stroke"], height=1).pack(side="top", fill="x")

    status_strip = tk.Frame(shell, bg=p["panel_alt"])
    status_strip.pack(side="bottom", fill="x")
    tk.Frame(status_strip, bg=p["stroke_soft"], height=1).place(
        x=0, y=0, relwidth=1)
    owner.status_bar = ttk.Label(
        status_strip, textvariable=owner.status, style="KingoStatus.TLabel",
        anchor="w", padding=(8, 3))
    owner.status_bar.pack(side="left", fill="x", expand=True)
    owner.status_gate = ttk.Label(
        status_strip, text="S202 只读", style="StatusCell.TLabel",
        padding=(10, 0))
    owner.status_gate.pack(side="right")
    tk.Frame(status_strip, bg=p["stroke_soft"], width=1).pack(
        side="right", fill="y", pady=4)

    body = ttk.Frame(shell, style="Shell.TFrame")
    body.pack(side="top", fill="both", expand=True, padx=6, pady=(3, 4))
    owner.visual_content = body
    return body


def make_scrollable_content(parent):
    """Return a full-width scrollable host for a dense page body.

    The Notebook page stays the navigation target; the canvas only hosts the
    view widgets. No school state or callback is moved.
    """
    p = PALETTE
    viewport = ttk.Frame(parent)
    viewport.pack(fill="both", expand=True)
    scrollbar = ttk.Scrollbar(viewport, orient="vertical")
    canvas = tk.Canvas(
        viewport, bg=p["background"], borderwidth=0,
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
    """Scroll the outer page on passive controls; keep grid/log wheels."""
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
        if widget.winfo_class() not in (
            "Treeview", "Text", "TScrollbar", "Scrollbar",
            "TEntry", "Entry", "Spinbox", "TSpinbox",
            "Listbox", "Canvas", "TCombobox",
        ):
            for event_name in ("<MouseWheel>", "<Button-4>", "<Button-5>"):
                widget.bind(event_name, on_wheel, add="+")
        for child in widget.winfo_children():
            attach(child)

    attach(content)


def update_responsive_shell(owner, _event=None) -> None:
    """Adapt chrome to narrow or short windows without touching state."""
    if _event is not None and _event.widget is not owner:
        return
    width = owner.winfo_width()
    if getattr(owner, "_shell_width", None) == width:
        return
    owner._shell_width = width
    usable = max(320, width - 150)
    status_bar = getattr(owner, "status_bar", None)
    if status_bar is not None:
        status_bar.configure(wraplength=usable)

    # Long help labels were authored with a fixed wraplength; clip them to
    # the real client width instead of letting them push the grid sideways.
    for attr, _caption in TABS:
        root = getattr(owner, attr, None)
        if root is None:
            continue
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
                if original >= 480:
                    desired = min(original, max(280, width - 90))
                    if current != desired:
                        widget.configure(wraplength=desired)
            pending.extend(widget.winfo_children())


def bind_responsive_shell(owner) -> None:
    owner.bind(
        "<Configure>", lambda event: update_responsive_shell(owner, event),
        add="+")
    update_responsive_shell(owner)
