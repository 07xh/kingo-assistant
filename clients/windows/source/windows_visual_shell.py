"""Dense Win32/WinForms-style visual shell for the existing Tk application.

The layout language deliberately mirrors a classic Windows desktop client:
a flat command strip, a real tab control, 1px group boxes, compact 9pt text,
a white data grid and a status bar. Nothing here touches the network,
configuration, sessions or course state. The API-only workbench and tools
are the two visible ``ttk.Notebook`` pages.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

PALETTE = {
    "background": "#F0F0F0",
    "surface": "#FFFFFF",
    "panel": "#F0F0F0",
    "panel_alt": "#E9E9E9",
    "ink": "#101010",
    "muted": "#5B5B5B",
    "stroke": "#A0A0A0",
    "stroke_soft": "#C8C8C8",
    "accent": "#0F6CBD",
    "accent_hover": "#0C5896",
    "accent_soft": "#DCEBFA",
    "selected": "#CCE4FF",
    "ok": "#0B6A0B",
    "warn": "#8A5300",
    "danger": "#A4262C",
    "grid_header": "#ECECEC",
}

# Point sizes, not pixels: the desktop client this layout mirrors is a
# 9pt-at-96dpi Win32 application, and Tk applies the display scale factor
# itself. Fixed pixel sizes would look cramped on a scaled display.
FONT_UI = ("Microsoft YaHei UI", 9)
FONT_BOLD = ("Microsoft YaHei UI", 9, "bold")
FONT_TITLE = ("Microsoft YaHei UI", 11, "bold")
FONT_SMALL = ("Microsoft YaHei UI", 8)
FONT_MONO = ("Consolas", 9)

# (notebook attribute, tab caption). Kept here so the shell owns the strip
# captions and the Application builds both tabs from one table.
TABS = (
    ("tab_home", "选课工作台"),
    ("tab_tools", "工具与日志"),
)

TOOLBAR_HEIGHT = 34
DEFAULT_SIZE = (1180, 790)
MINIMUM_SIZE = (960, 640)


def apply_light_theme(owner) -> None:
    """Install the dense desktop theme once; callers never re-run it."""
    p = PALETTE
    owner.configure(bg=p["background"])
    style = ttk.Style(owner)
    if "clam" in style.theme_names():
        style.theme_use("clam")

    # --- containers -------------------------------------------------
    style.configure("TFrame", background=p["background"])
    style.configure("Shell.TFrame", background=p["background"])
    style.configure("Surface.TFrame", background=p["surface"])
    style.configure("Panel.TFrame", background=p["panel"])
    style.configure("Card.TFrame", background=p["surface"])

    # --- text -------------------------------------------------------
    style.configure(
        "TLabel", background=p["background"], foreground=p["ink"],
        font=FONT_UI)
    style.configure(
        "Surface.TLabel", background=p["surface"],
        foreground=p["ink"], font=FONT_UI)
    style.configure(
        "Muted.TLabel", background=p["background"],
        foreground=p["muted"], font=FONT_SMALL)
    style.configure(
        "SurfaceMuted.TLabel", background=p["surface"],
        foreground=p["muted"], font=FONT_SMALL)
    style.configure(
        "Hint.TLabel", background=p["background"],
        foreground=p["warn"], font=FONT_SMALL)
    style.configure(
        "SurfaceHint.TLabel", background=p["surface"],
        foreground=p["warn"], font=FONT_SMALL)
    style.configure(
        "KingoTitle.TLabel", background=p["background"],
        foreground=p["ink"], font=FONT_TITLE)
    style.configure(
        "KingoSubtitle.TLabel", background=p["background"],
        foreground=p["muted"], font=FONT_SMALL)
    style.configure(
        "KingoStatus.TLabel", background=p["panel_alt"],
        foreground=p["ink"], font=FONT_SMALL)
    style.configure(
        "StatusCell.TLabel", background=p["panel_alt"],
        foreground=p["muted"], font=FONT_SMALL)

    # --- group boxes (WinForms GroupBox) ----------------------------
    for name, fill in (("TLabelframe", p["background"]),
                       ("Card.TLabelframe", p["surface"])):
        style.configure(
            name, background=fill, bordercolor=p["stroke"],
            lightcolor=p["stroke"], darkcolor=p["stroke"],
            borderwidth=1, relief="solid", padding=2)
        style.configure(
            f"{name}.Label", background=fill, foreground=p["ink"],
            font=FONT_BOLD, padding=(2, 0))
    style.configure(
        "KingoStep.TLabelframe", background=p["background"],
        bordercolor=p["stroke"], borderwidth=1, relief="solid")
    style.configure(
        "KingoStep.TLabelframe.Label", background=p["background"],
        foreground=p["ink"], font=FONT_BOLD)

    # --- buttons ----------------------------------------------------
    style.configure(
        "TButton", padding=(10, 4), font=FONT_UI,
        background=p["panel_alt"], foreground=p["ink"],
        bordercolor=p["stroke"], borderwidth=1, relief="solid",
        focuscolor=p["accent_soft"], anchor="center")
    style.map(
        "TButton",
        background=[("disabled", p["panel"]), ("pressed", p["accent_soft"]),
                    ("active", p["accent_soft"])],
        foreground=[("disabled", p["muted"])],
        bordercolor=[("active", p["accent"])])
    style.configure(
        "KingoBar.TButton", padding=(7, 3), font=FONT_UI,
        background=p["panel"], foreground=p["ink"],
        bordercolor=p["panel"], borderwidth=1, relief="flat")
    style.map(
        "KingoBar.TButton",
        background=[("active", p["accent_soft"]),
                    ("pressed", p["selected"])],
        bordercolor=[("active", p["stroke_soft"])])
    # The single wide call-to-action on the home page.
    style.configure(
        "KingoPrimary.TButton", padding=(10, 5), font=FONT_BOLD,
        background=p["accent"], foreground=p["surface"],
        bordercolor=p["accent"], borderwidth=1, relief="solid")
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
        "Surface.TCheckbutton", background=p["surface"],
        foreground=p["ink"], font=FONT_UI)
    style.map(
        "Surface.TCheckbutton", background=[("active", p["surface"])])
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
        "Treeview", rowheight=23, background=p["surface"],
        fieldbackground=p["surface"], foreground=p["ink"],
        borderwidth=1, relief="solid", font=FONT_UI)
    style.map(
        "Treeview",
        background=[("selected", p["selected"])],
        foreground=[("selected", p["ink"])])
    style.configure(
        "Treeview.Heading", background=p["grid_header"],
        foreground=p["ink"], padding=(6, 4), font=FONT_BOLD,
        relief="raised", borderwidth=1)
    style.map(
        "Treeview.Heading", background=[("active", p["accent_soft"])])

    # --- tab control ------------------------------------------------
    # The strip stays visible: this build navigates with real Win32-style
    # tabs, so no ``style.layout(..., [])`` may hide the notebook again.
    style.configure(
        "Kingo.Content.TNotebook", background=p["background"],
        borderwidth=1, tabmargins=(4, 4, 4, 0), padding=0)
    # clam only paints a raised tab when the light/dark/border colours are
    # set explicitly; background alone leaves every tab flat white.
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
        foreground=[("selected", p["ink"])],
        expand=[("selected", (0, 0, 0, 0))])


def make_visual_shell(owner) -> ttk.Frame:
    """Build command strip + status bar and return the page host frame."""
    p = PALETTE
    shell = tk.Frame(owner, bg=p["background"])
    shell.pack(fill="both", expand=True)
    owner.visual_shell = shell

    strip = tk.Frame(shell, bg=p["panel"], height=TOOLBAR_HEIGHT)
    strip.pack(side="top", fill="x")
    strip.pack_propagate(False)
    owner.toolbar = strip

    brand = tk.Label(
        strip, text="KingoAssistant", bg=p["panel"], fg=p["accent"],
        font=("Microsoft YaHei UI", 10, "bold"))
    brand.pack(side="left", padx=(10, 8), pady=6)
    tk.Label(
        strip, text="华水选课助手 · 只读监测", bg=p["panel"], fg=p["muted"],
        font=FONT_SMALL).pack(side="left", pady=8)
    tk.Frame(strip, bg=p["stroke_soft"], width=1).pack(
        side="left", fill="y", padx=8, pady=7)
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
    body.pack(side="top", fill="both", expand=True, padx=6, pady=4)
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
