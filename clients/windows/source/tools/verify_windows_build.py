"""Fail-closed *native Windows* EXE verification after PyInstaller packaging.

This module NEVER authenticates or contacts a school. It starts the packaged
EXE only with offline diagnostic flags. Results are deliberately labeled as
packaging/GUI verification, NOT verification of any university API.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
from datetime import datetime, timezone


class BuildVerificationError(RuntimeError):
    pass


def verify(exe: Path, *, system: str | None = None) -> dict:
    actual_system = platform.system() if system is None else system
    if actual_system != "Windows":
        raise BuildVerificationError("Native EXE verification must run on Windows, not Linux/Wine")
    if not exe.is_file() or not exe.name.lower().endswith(".exe"):
        raise BuildVerificationError("Windows executable not found")
    with exe.open("rb") as handle:
        magic = handle.read(2)
    if magic != b"MZ":
        raise BuildVerificationError("Artifact is not a Windows PE executable")

    # Timeouts prevent a broken GUI executable from stalling CI indefinitely.
    checks = (("offline_self_test", "--self-test", 60),
              ("native_tk_gui_smoke", "--smoke-gui", 45))
    passed = {}
    for name, flag, timeout in checks:
        try:
            result = subprocess.run([str(exe.resolve()), flag],
                                    timeout=timeout, check=False, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
        except subprocess.TimeoutExpired as error:
            raise BuildVerificationError(f"{name} timed out after {timeout}s") from error
        except OSError as error:
            raise BuildVerificationError(f"{name} could not start") from error
        if result.returncode != 0:
            raise BuildVerificationError(f"{name} failed with exit code {result.returncode}")
        passed[name] = "passed"

    with exe.open("rb") as handle:
        sha256 = hashlib.file_digest(handle, "sha256").hexdigest()
    return {
        "platform": "Windows",
        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
        "artifact": exe.name,
        "size_bytes": exe.stat().st_size,
        "sha256": sha256,
        "checks": passed,
        "school_api_verified": False,
        "automatic_enrollment_implemented": False,
    }


def main() -> int:
    exe = Path(sys.argv[1]) if len(sys.argv) == 2 else Path("dist/KingoAssistant-Windows.exe")
    output = exe.parent
    # Delete stale success markers *before* inspection: failed runs cannot
    # accidentally leave behind proof for a previous version.
    for path in (output / "SHA256.txt", output / "build-verification.json"):
        path.unlink(missing_ok=True)
    try:
        result = verify(exe)
    except BuildVerificationError as error:
        print(f"[FAILED] {error}", file=sys.stderr)
        return 1
    (output / "SHA256.txt").write_text(
        f"{result['sha256']}  {result['artifact']}\n", encoding="utf-8")
    (output / "build-verification.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("[PASS] Native Windows EXE self-test + Tk GUI smoke test")
    print("[NOTE] University login and live course retrieval were NOT tested")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
