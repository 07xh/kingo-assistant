"""Optional screenshot of the *running* Tk application, not a mockup.

Uses only local template/config and never starts school network calls.
Intended for a Windows Actions desktop with a display surface; an absent
interactive desktop may prevent a screenshot without affecting EXE tests.
"""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

from PIL import ImageGrab

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from windows_gui import Application


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: export_visual_preview.py <output.png>")
    output = Path(sys.argv[1])
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="kingo-visual-preview-") as home:
        with patch("windows_gui.app_dir", return_value=Path(home)):
            app = Application()
            try:
                app.geometry("1210x820+70+70")
                app.update_idletasks()
                app.deiconify()
                app.lift()
                app.update()
                left, top = app.winfo_rootx(), app.winfo_rooty()
                width = app.winfo_width()
                height = app.winfo_height()
                if width < 700 or height < 500:
                    raise RuntimeError("Desktop surface unavailable for screenshot")
                image = ImageGrab.grab(
                    bbox=(left, top, left + width, top + height))
                if image.size != (width, height):
                    raise RuntimeError("Invalid screenshot dimensions")
                # Avoid publishing a blank image caused by an off-screen
                # Windows service display.
                if image.convert("RGB").getbbox() is None:
                    raise RuntimeError("Blank screenshot")
                image.save(output, format="PNG")
                print(f"Visual preview of real Tk app: {output} {width}x{height}")
            finally:
                app.destroy()


if __name__ == "__main__":
    main()
