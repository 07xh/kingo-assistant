"""Capture actual running Tk windows at a screen-fitting size.

Screenshots use a temporary local template only; do not log into the school
or issue HTTP requests. The CI desktop may be unavailable, so this remains
an optional artifact rather than a release gate.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from PIL import ImageGrab

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from windows_gui import Application


def save_view(app, output: Path) -> None:
    app.update_idletasks()
    app.update()
    left, top = app.winfo_rootx(), app.winfo_rooty()
    width, height = app.winfo_width(), app.winfo_height()
    if width < 750 or height < 520:
        raise RuntimeError("Desktop surface unavailable for screenshot")
    image = ImageGrab.grab(bbox=(left, top, left + width, top + height))
    if image.size != (width, height):
        raise RuntimeError("Invalid screenshot dimensions")
    if image.convert("RGB").getbbox() is None:
        raise RuntimeError("Blank desktop screenshot")
    image.save(output, format="PNG")
    print(f"Real Tk view: {output} ({width}x{height})")


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: export_visual_preview.py <output.png>")
    output = Path(sys.argv[1])
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="kingo-responsive-preview-") as home:
        with patch("windows_gui.app_dir", return_value=Path(home)):
            app = Application()
            try:
                # Fit within the CI desktop instead of cropping the lower
                # course list behind the Windows taskbar.
                sw, sh = app.winfo_screenwidth(), app.winfo_screenheight()
                width = min(1100, max(880, sw - 48))
                height = min(740, max(560, sh - 128))
                app.geometry(f"{width}x{height}+12+12")
                app.update_idletasks()
                app.deiconify()
                app.lift()

                # The home page is a fixed dense layout now, so one capture
                # shows the whole workflow: commands, grid and submit gate.
                app.notebook.select(app.tab_home)
                save_view(app, output)

                app.notebook.select(app.tab_manual)
                save_view(app, output.with_name(
                    "KingoAssistant-Manual-preview.png"))

                app.notebook.select(app.tab_tools)
                app.tools_scroll_canvas.yview_moveto(1)
                save_view(app, output.with_name(
                    "KingoAssistant-Tools-preview.png"))
            finally:
                app.destroy()


if __name__ == "__main__":
    main()
