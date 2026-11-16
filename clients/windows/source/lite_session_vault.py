"""Windows-account-protected session persistence for the optional Lite UI.

Opt-in only. Uses the Windows DPAPI CurrentUser scope, not a passphrase,
portable encryption key, plaintext cookie file, or stored school password.
The encrypted token is useful ONLY until the school revokes/expires it; the
app never asserts session validity based solely on a local encrypted blob.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time

HOST = "jwmis.ncwu.edu.cn"
SCHEMA = 1
MAX_LOCAL_AGE_SECONDS = 7 * 24 * 60 * 60
MAX_BLOB_BYTES = 16384
_ACCOUNT = re.compile(r"^[0-9A-Za-z_.-]{3,64}$")
_COOKIE = re.compile(r"^[A-Za-z0-9_-]{1,80}=[^;\s]{1,2048}(?:; [A-Za-z0-9_-]{1,80}=[^;\s]{1,2048})*$")


@dataclass(frozen=True)
class SavedSchoolSession:
    username: str
    cookie: str
    created: float


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _dpapi(data: bytes, *, encrypt: bool) -> bytes:
    if sys.platform != "win32":
        raise OSError("Windows DPAPI is required")
    if not data or len(data) > MAX_BLOB_BYTES:
        raise ValueError("invalid secret length")
    # WinDLL and system libraries are resolved only on Windows. The API
    # handles per-user encryption keys and can protect tokens without
    # exporting a reusable master key into the repository.
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    operation = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
    operation.argtypes = [
        ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.POINTER(_Blob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_Blob),
    ]
    operation.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    in_buffer = ctypes.create_string_buffer(data, len(data))
    input_blob = _Blob(len(data), ctypes.cast(in_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output_blob = _Blob()
    try:
        if not operation(ctypes.byref(input_blob), None, None, None, None, 0x01, ctypes.byref(output_blob)):
            raise OSError("Windows DPAPI operation failed")
        if not output_blob.pbData or output_blob.cbData <= 0 or output_blob.cbData > MAX_BLOB_BYTES:
            raise ValueError("Windows DPAPI output is invalid")
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        if output_blob.pbData:
            kernel32.LocalFree(ctypes.cast(output_blob.pbData, ctypes.c_void_p))


def _valid_cookie(value: str) -> bool:
    if not isinstance(value, str) or not 1 <= len(value) <= 2000:
        return False
    if not _COOKIE.fullmatch(value):
        return False
    names = [pair.split("=", 1)[0] for pair in value.split("; ")]
    return "JSESSIONID" in names and len(set(names)) == len(names)


class WindowsSessionVault:
    """User-scoped encrypted credential; never stores the school password."""

    def __init__(self, directory: Path):
        self.directory = Path(directory)
        self.path = self.directory / "official-session.dpapi"

    @property
    def supported(self) -> bool:
        return sys.platform == "win32"

    def save(self, username: str, cookie: str) -> None:
        if not self.supported:
            raise OSError("Windows-only credential protection")
        if not isinstance(username, str) or not _ACCOUNT.fullmatch(username):
            raise ValueError("invalid account")
        if not _valid_cookie(cookie):
            raise ValueError("invalid school session")
        record = {
            "version": SCHEMA, "origin": HOST, "account": username,
            "cookie": cookie, "created": time.time(),
        }
        encrypted = _dpapi(json.dumps(record, separators=(",", ":"), ensure_ascii=False).encode("utf-8"), encrypt=True)
        if len(encrypted) > MAX_BLOB_BYTES:
            raise ValueError("encrypted session too large")
        self.directory.mkdir(parents=True, exist_ok=True)
        if self.directory.is_symlink() or self.path.is_symlink():
            raise OSError("unsafe credentials directory")
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.directory, prefix=".official-session-",
                                             suffix=".tmp", delete=False) as file:
                temporary = Path(file.name)
                os.chmod(temporary, 0o600)
                file.write(encrypted)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def load(self) -> SavedSchoolSession | None:
        if not self.supported:
            return None
        try:
            if self.path.is_symlink() or not self.path.is_file():
                return None
            if not 1 <= self.path.stat().st_size <= MAX_BLOB_BYTES:
                return None
            decrypted = _dpapi(self.path.read_bytes(), encrypt=False)
            record = json.loads(decrypted.decode("utf-8", "strict"))
            if not isinstance(record, dict) or set(record) != {
                "version", "origin", "account", "cookie", "created",
            }:
                return None
            created = record["created"]
            if (record["version"] != SCHEMA or record["origin"] != HOST
                    or not isinstance(created, (float, int)) or isinstance(created, bool)
                    or not (0 <= time.time() - float(created) <= MAX_LOCAL_AGE_SECONDS)
                    or not isinstance(record["account"], str)
                    or not _ACCOUNT.fullmatch(record["account"])
                    or not _valid_cookie(record["cookie"])):
                return None
            return SavedSchoolSession(record["account"], record["cookie"], float(created))
        except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError):
            return None

    def clear(self) -> None:
        if self.path.is_symlink():
            raise OSError("unsafe credentials path")
        self.path.unlink(missing_ok=True)
