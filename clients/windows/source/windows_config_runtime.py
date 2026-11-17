"""App-owned Windows config paths and isolated template/load operations.

No browser, network, school credentials, or GUI controls live here.
File creation is idempotent: existing user config is never overwritten.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys
from typing import Callable

from core.desktop_settings import load_config, resolve_state_file


def app_dir() -> Path:
    """Never write user data inside the PyInstaller bundle/temp folder."""
    if os.name == "nt":
        root = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if root:
            return Path(root) / "KingoAssistantReadOnly"
    return Path.home() / ".kingo-assistant-readonly"


def bundled_config() -> Path:
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return root / "config.example.yaml"


def bundled_demo() -> Path:
    """Read-only fictional fixtures embedded in both source and one-file EXE."""
    root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return root / "demo"



def ensure_user_config(destination_directory: Path, template_path: Path) -> Path:
    """Install template only for a fresh user configuration."""
    destination_directory.mkdir(parents=True, exist_ok=True)
    dest = destination_directory / "config.yaml"
    if not dest.exists():
        if not template_path.is_file():
            raise FileNotFoundError("安装资源中缺少 config.example.yaml 模板")
        shutil.copyfile(template_path, dest)
    return dest


def load_validated_config(
    path: Path, *, offline: bool = False,
    user_directory: Path | None = None,
    validator: Callable,
) -> dict:
    """Same load/resolve/validate ordering as the previous GUI wrapper."""
    config = load_config(path)
    if not offline:
        if user_directory is None:
            raise ValueError("运行配置必须指定本地应用数据目录")
        config = resolve_state_file(config, user_directory)
    validator(config, check_only=offline)
    return config
