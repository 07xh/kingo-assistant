"""Windows system-tray adapter for KingoAssistant.

Tray callbacks run outside Tk's owner thread. They only enqueue commands; the
Tk event loop consumes them, so no widget is touched by the tray thread.
"""
from __future__ import annotations

import sys
import threading
from typing import Callable


class TrayController:
    def __init__(self, post: Callable[[str], None]):
        self._post = post
        self._icon = None
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        """Return False when no usable Windows tray backend is available."""
        if self._icon is not None:
            return True
        if sys.platform != "win32":
            return False
        try:
            from PIL import Image, ImageDraw
            import pystray

            image = Image.new("RGB", (64, 64), "#174a74")
            brush = ImageDraw.Draw(image)
            brush.rounded_rectangle((10, 10, 54, 54), radius=9, fill="#e6f3ff")
            brush.line((22, 34, 29, 41, 44, 23), fill="#174a74", width=6)
            icon = pystray.Icon(
                "KingoAssistant",
                image,
                "KingoAssistant · 只读监测/预约",
                menu=pystray.Menu(
                    pystray.MenuItem(
                        "显示主窗口",
                        lambda *_: self._post("tray_restore"),
                        default=True,
                    ),
                    pystray.MenuItem(
                        "完全退出",
                        lambda *_: self._post("tray_quit"),
                    ),
                ),
            )
            self._icon = icon
            self._thread = threading.Thread(
                target=self._run, args=(icon,),
                name="KingoAssistantTray", daemon=True,
            )
            self._thread.start()
            return True
        except Exception:
            self._icon = None
            return False

    def _run(self, icon) -> None:
        try:
            icon.run()
        except Exception:
            # Restore a hidden window if the native tray loop fails to start.
            self._post("tray_error")
        finally:
            if self._icon is icon:
                self._icon = None

    def stop(self) -> None:
        icon = self._icon
        self._icon = None
        if icon is not None:
            try:
                icon.stop()
            except Exception:
                pass
