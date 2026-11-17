"""Reusable Tk read-only result widgets for KingoAssistant.

The owner/parent controls the lifecycle. This module does not access the
network, user configuration, sessions, course statuses, or school IDs.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk


def _open_readonly_popup(
    owner, *, title: str, size: str, message: str, wraplength: int,
):
    """Shared static results window; no school requests or mutable state."""
    popup = tk.Toplevel(owner)
    popup.title(title)
    popup.geometry(size)
    popup.transient(owner)
    ttk.Label(popup, text=message, wraplength=wraplength).pack(
        anchor="w", padx=12, pady=(12, 8))
    return popup


def _create_readonly_tree(
    parent, *, columns: tuple[str, ...], titles: tuple[str, ...],
    widths: tuple[int, ...], minwidth: int, height: int | None = None,
):
    """Configure headings and dimensions consistently without changing rows."""
    if not (len(columns) == len(titles) == len(widths)):
        raise ValueError("readonly table column specification mismatch")
    options = {"columns": columns, "show": "headings"}
    if height is not None:
        options["height"] = height
    table = ttk.Treeview(parent, **options)
    for name, title, width in zip(columns, titles, widths):
        table.heading(name, text=title)
        table.column(name, width=width, minwidth=minwidth)
    return table
