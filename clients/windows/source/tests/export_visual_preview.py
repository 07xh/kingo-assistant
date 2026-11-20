"""Capture actual running Tk windows at a screen-fitting size.

Screenshots use a temporary local template only; do not log into the school
or issue HTTP requests. The CI desktop may be unavailable, so this remains
an optional artifact rather than a release gate.
"""
from __future__ import annotations

from inspect import signature
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

from PIL import ImageGrab

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from windows_gui import Application
from core.ncwu_live_elective import LiveElectiveCourse, LiveElectiveSnapshot


def save_view(app, output: Path) -> None:
    app.update_idletasks()
    app.update()
    left, top = app.winfo_rootx(), app.winfo_rooty()
    width, height = app.winfo_width(), app.winfo_height()
    if width < 750 or height < 520:
        raise RuntimeError("Desktop surface unavailable for screenshot")
    if sys.platform == "win32" and "window" in signature(ImageGrab.grab).parameters:
        image = ImageGrab.grab(window=int(app.wm_frame(), 0))
    else:
        image = ImageGrab.grab(bbox=(left, top, left + width, top + height))
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
                app.geometry("+16+48")
                app.update_idletasks()
                app.deiconify()
                app.lift()

                app.notebook.select(app.tab_home)
                app.demo_next()
                app.live_catalog_status.set("离线预览 · 示例课程")
                app.show_live_catalog_rows(LiveElectiveSnapshot(
                    "catalog_read", "catalog", "reported_open",
                    (LiveElectiveCourse("工程制图", "DEMO101", "01", "张老师", "可选", "—"),
                     LiveElectiveCourse("结构力学", "DEMO203", "02", "李老师", "待核对", "—"),
                     LiveElectiveCourse("大学英语", "DEMO305", "03", "王老师", "可选", "—")),
                    3,
                ))
                save_view(app, output)

                app.notebook.select(app.tab_account)
                save_view(app, output.with_name(
                    "KingoAssistant-Account-preview.png"))

                app.notebook.select(app.tab_tools)
                app.tools_scroll_canvas.yview_moveto(1)
                save_view(app, output.with_name(
                    "KingoAssistant-Tools-preview.png"))
            finally:
                app.destroy()


if __name__ == "__main__":
    main()
