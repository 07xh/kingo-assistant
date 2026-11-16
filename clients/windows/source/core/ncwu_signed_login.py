"""EXE-native, first-party normal-form login for the NCWU Kingo portal.

Based on the user's November 14 first-party Javascript and HTTP capture.
This is NOT the xiqueer/QR shortcut, browser control or a replay of HAR
credentials. The server supplies a fresh form session and temporary key.
CAPTCHA, SMS, MFA and CAS redirects are never bypassed or auto-solved.

One explicit user action: initial GET, encryption script metadata GET,
two ephemeral-value GETs, one login POST, one YearTerm account readback.
No retries, no enrollment writes, no cookie/credential persistence.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from typing import Callable, Mapping
from urllib.parse import urlsplit

import httpx
from bs4 import BeautifulSoup

from core.native_login import NativeLoginResult, _same_origin_cookies
from core.ncwu_legacy_encoder import encode_legacy_strenc

_HOST = "jwmis.ncwu.edu.cn"
_ROOT = "https://" + _HOST
_LOGIN_PAGE = "/hsjw/cas/login.action"
_SETTINGS = "/hsjw/custom/js/SetKingoEncypt.jsp"
_TEMP = "/hsjw/frame/homepage"
_POST = "/hsjw/cas/logon.action"
_YEAR_TERM = "/hsjw/jw/common/showYearTerm.action"
_IDENTIFIER = re.compile(r"^[0-9A-Za-z_.-]{3,64}$")
_SESSION_RE = re.compile(r"""var\s+_sessionid\s*=\s*['"]([0-9A-Fa-f]{32})['"]""")
_SSESSION_RE = re.compile(r"""var\s+_ssessionid\s*=\s*['"]([0-9A-Fa-f]{32})['"]""")
_YZMBT_RE = re.compile(r"""var\s+yzmbt\s*=\s*['"]([01])['"]""")
_DESKEY_RE = re.compile(r"""var\s+_deskey\s*=\s*['"]([A-Za-z0-9]{0,64})['"]""")
_TIME_RE = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$")
_KEY_RE = re.compile(r"^[0-9A-Za-z]{8,64}$")

# Conservative local guard, NOT a claim about the school's published quota.
# Each press consumes one attempt. No automatic retries or fast polling.
_LAST_ATTEMPT = 0.0
_NEXT_ALLOWED = 0.0
_COOLDOWN_SECONDS = 60.0


def _respect_retry_after(response: httpx.Response) -> None:
    """Observe numeric server Retry-After; never retry automatically."""
    global _NEXT_ALLOWED
    if response.status_code != 429:
        return
    raw = response.headers.get("Retry-After", "")
    try:
        seconds = int(raw)
    except (TypeError, ValueError):
        seconds = 60
    seconds = max(60, min(3600, seconds))
    _NEXT_ALLOWED = max(_NEXT_ALLOWED, time.monotonic() + seconds)


def validate_ncwulogin_entry(config: Mapping) -> str | None:
    """School-only official origin; no arbitrary endpoint or verified flags."""
    if not isinstance(config, Mapping):
        return "invalid_contract"
    try:
        url = urlsplit(config.get("base_url"))
        if (url.scheme != "https" or url.hostname != _HOST
                or url.netloc != _HOST or url.path not in ("", "/")
                or url.query or url.fragment or url.username or url.password):
            return "invalid_contract"
    except (TypeError, ValueError):
        return "invalid_contract"
    return None


def _content(resp: httpx.Response, *, max_bytes: int, mime: tuple[str, ...]) -> str:
    if resp.status_code != 200:
        raise ValueError("unexpected HTTP status")
    if len(resp.content) > max_bytes:
        raise ValueError("oversized official response")
    content_type = resp.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type not in mime:
        raise ValueError("unexpected content type")
    return resp.content.decode("utf-8-sig", errors="strict")


def _form_bootstrap(html: str) -> tuple[str, str]:
    if not (_SESSION_RE.search(html) and _SSESSION_RE.search(html)):
        raise ValueError("missing official signed-login session markers")
    yzmbt = _YZMBT_RE.search(html)
    flag = BeautifulSoup(html, "html.parser").find(id="hid_flag")
    if (yzmbt is None or yzmbt.group(1) != "0"
            or flag is None or flag.get("value") != "1"):
        raise PermissionError("verification required by school login form")
    return _SESSION_RE.search(html).group(1), _SSESSION_RE.search(html).group(1)


def _password_properties(username: str, password: str) -> tuple[int, int, int, int]:
    mask = 0
    for c in password:
        code = ord(c)
        mask |= 8 if 48 <= code <= 57 else 4 if 97 <= code <= 122 else (
            2 if 65 <= code <= 90 else 1)
    length = len(password.encode("utf-16-be")) // 2
    includes_username = int(username.strip().lower() in password.strip().lower())
    allowed = int(password != username and length >= 6)
    return mask, length, includes_username, allowed


def _sign_login_form(
    username: str, password: str, session_id: str, signed_session_id: str,
    deskey: str, temporary_key: str, timestamp: str,
) -> dict[str, str]:
    if (not _KEY_RE.fullmatch(temporary_key)
            or not _TIME_RE.fullmatch(timestamp)
            or not re.fullmatch(r"[0-9A-Fa-f]{32}", session_id)
            or not re.fullmatch(r"[0-9A-Fa-f]{32}", signed_session_id)
            or not re.fullmatch(r"[A-Za-z0-9]{0,64}", deskey)):
        raise ValueError("invalid fresh school key or timestamp")
    mask, length, includes, policy = _password_properties(username, password)
    encoded_user = base64.b64encode((username + ";;" + session_id).encode("utf-8")).decode("ascii")
    # Exactly the order and literal names observed in the school's normal
    # loginbar.js, no QR/xiqueer special login-method shortcuts.
    inner = (
        "_u=" + encoded_user
        + "&_p=" + password
        + "&randnumber=&isPasswordPolicy=" + str(policy)
        + "&txt_mm_expression=" + str(mask)
        + "&txt_mm_length=" + str(length)
        + "&txt_mm_userzh=" + str(includes)
        + "&hid_flag=1&hidlag=1&hid_dxyzm="
    )
    # The captured site's packed md5.js uses 8-bit JS code units, not
    # Python UTF-8 bytes. Preserve its actual low-byte UTF-16 behavior for
    # non-ASCII passwords rather than inventing a different signature.
    def old_js_md5(value: str) -> str:
        utf16 = value.encode("utf-16-be", errors="surrogatepass")
        return hashlib.md5(utf16[1::2]).hexdigest()

    signature = hashlib.md5(
        (old_js_md5(inner) + old_js_md5(timestamp)).encode("ascii")
    ).hexdigest()
    ciphertext = encode_legacy_strenc(inner, temporary_key)
    return {
        "params": base64.b64encode(ciphertext.encode("ascii")).decode("ascii"),
        "token": signature, "timestamp": timestamp, "deskey": deskey,
        "ssessionid": signed_session_id,
    }


def _school_response(resp: httpx.Response) -> str:
    if resp.status_code == 429 or resp.status_code == 403:
        _respect_retry_after(resp)
        return "restricted"
    if 300 <= resp.status_code < 400:
        return "challenge_required"
    if resp.status_code != 200 or len(resp.content) > 16_384:
        return "unavailable"
    try:
        record = json.loads(resp.content.decode("utf-8-sig", errors="strict"))
    except (ValueError, UnicodeError):
        return "protocol_changed"
    if not isinstance(record, dict):
        return "protocol_changed"
    status = str(record.get("status", ""))
    if status == "200":
        return "ok"
    if status in ("505", "407", "405"):
        return "challenge_required"
    return "login_rejected"


def _validated_yearterm(resp: httpx.Response, username: str) -> str:
    if resp.status_code in (401, 302, 301, 303, 307, 308):
        return "expired"
    if resp.status_code in (403, 429):
        _respect_retry_after(resp)
        return "restricted"
    if resp.status_code != 200 or len(resp.content) > 8192:
        return "unavailable"
    ct = resp.headers.get("content-type", "").split(";", 1)[0].lower().strip()
    if ct not in ("text/plain", "application/json"):
        return "protocol_changed"
    try:
        obj = json.loads(resp.content.decode("utf-8-sig", errors="strict"))
        required = ("xn", "xqM", "xqName", "jwUserYeartermId", "userCode")
        if (not isinstance(obj, dict)
                or any(not isinstance(obj.get(k), str) or not obj[k].strip()
                       for k in required)):
            return "protocol_changed"
        if hmac.compare_digest(obj["userCode"].encode("utf-8"),
                               username.encode("utf-8")):
            return "verified"
        return "identity_mismatch"
    except (UnicodeError, ValueError, TypeError):
        return "protocol_changed"


async def login_ncwusigned(
    config: Mapping, username: str, password: str, *,
    http_factory: Callable[[], httpx.AsyncClient] | None = None,
    enforce_local_cooldown: bool = True,
) -> NativeLoginResult:
    """The normal school's signed form + independent account-bound readback."""
    global _LAST_ATTEMPT, _NEXT_ALLOWED
    if validate_ncwulogin_entry(config) is not None:
        return NativeLoginResult("invalid_contract")
    if (not isinstance(username, str) or not _IDENTIFIER.fullmatch(username)
            or not isinstance(password, str) or not password
            or len(password.encode("utf-16-be")) // 2 > 30
            or any(c in password for c in "&=\r\n\0")):
        return NativeLoginResult("invalid_credentials")
    now = time.monotonic()
    if enforce_local_cooldown and now < _NEXT_ALLOWED:
        return NativeLoginResult("local_cooldown")
    if enforce_local_cooldown:
        _LAST_ATTEMPT = now
        _NEXT_ALLOWED = now + _COOLDOWN_SECONDS
    if http_factory is None:
        http_factory = lambda: httpx.AsyncClient(
            follow_redirects=False, trust_env=False,
            timeout=httpx.Timeout(connect=5, read=10, write=10, pool=5),
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
        )
    try:
        async with http_factory() as client:
            async def fetch(path, *, params=None):
                response = await client.get(_ROOT + path, params=params, follow_redirects=False)
                if response.status_code in (403, 429):
                    _respect_retry_after(response)
                    raise PermissionError("restricted by official service")
                if response.status_code != 200:
                    raise ValueError("official site changed or requires SSO")
                return response

            bootstrap = await fetch(_LOGIN_PAGE)
            page = _content(bootstrap, max_bytes=512_000, mime=("text/html",))
            session_id, signed_session = _form_bootstrap(page)
            enc = await fetch(_SETTINGS)
            script = _content(
                enc, max_bytes=50_000, mime=("text/javascript", "application/javascript"))
            found = _DESKEY_RE.search(script)
            if found is None:
                return NativeLoginResult("protocol_changed")
            deskey = found.group(1)
            key_resp = await fetch(_TEMP, params={"method": "getTempDeskey"})
            time_resp = await fetch(_TEMP, params={"method": "getTempNowtime"})
            tmp_key = _content(key_resp, max_bytes=100, mime=("text/plain",)).strip()
            timestamp = _content(time_resp, max_bytes=100, mime=("text/plain",)).strip()
            payload = _sign_login_form(
                username, password, session_id, signed_session,
                deskey, tmp_key, timestamp)
            login_response = await client.post(
                _ROOT + _POST, data=payload, follow_redirects=False,
                headers={"Accept": "text/plain,application/json"},
            )
            result = _school_response(login_response)
            if result != "ok":
                return NativeLoginResult(result)
            # The same httpx client and cookie jar carry the authenticated
            # server session. No browser cookies and no token replay.
            term_resp = await client.post(
                _ROOT + _YEAR_TERM, follow_redirects=False,
                headers={"Accept": "text/plain,application/json"},
            )
            proof = _validated_yearterm(term_resp, username)
            if proof != "verified":
                return NativeLoginResult(proof)
            cookie = _same_origin_cookies(client, _HOST)
            if not cookie:
                return NativeLoginResult("unavailable")
            return NativeLoginResult("verified", cookie, _HOST)
    except PermissionError as e:
        # Includes CAPTCHA requirements from the page; do not switch to
        # unprotected login methods or retry with different credentials.
        return NativeLoginResult(
            "challenge_required" if "verification required" in str(e)
            else "restricted")
    except (ValueError, UnicodeError):
        return NativeLoginResult("protocol_changed")
    except Exception:
        # No exception can echo a password, session ID, dynamic key or HTTP body.
        return NativeLoginResult("unavailable")
