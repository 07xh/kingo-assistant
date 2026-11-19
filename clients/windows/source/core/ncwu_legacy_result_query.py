"""Build NCWU selected-results query only from this session's YearTerm proof.

Observed Nov 03 JS: base64("xn=" + xn + "&xq=" + xq + "&xh=" + xh).
Neither a historical HAR token nor user-entered YAML identity is accepted.
"""
from __future__ import annotations

import base64
import hmac
import json
import re

_REQUIRED = ("xn", "xqM", "xqName", "jwUserYeartermId", "userCode")
_ACCOUNT = re.compile(r"^[0-9A-Za-z_.-]{3,64}$")
_YEAR = re.compile(r"^20[0-9]{2}$")
_TERM = re.compile(r"^[0-9]$")


class CurrentResultQueryError(ValueError):
    """Reason code only; must never print response or account identifiers."""

    def __init__(self, code: str):
        if code not in ("identity_unverified", "identity_mismatch", "invalid_response"):
            code = "invalid_response"
        self.code = code
        super().__init__(code)


def valid_current_account(value: object) -> bool:
    return isinstance(value, str) and _ACCOUNT.fullmatch(value) is not None


def build_current_result_params(response: bytes, *, verified_user_code: str) -> str:
    """Return an ephemeral Base64 query using only a verified session account."""
    if not valid_current_account(verified_user_code):
        raise CurrentResultQueryError("identity_unverified")
    if not isinstance(response, bytes) or not 0 < len(response) <= 8192:
        raise CurrentResultQueryError("invalid_response")
    try:
        record = json.loads(response.decode("utf-8-sig", errors="strict"))
    except (ValueError, UnicodeError, RecursionError):
        raise CurrentResultQueryError("invalid_response") from None
    if (not isinstance(record, dict)
            or any(not isinstance(record.get(key), str)
                   or not record[key].strip() or len(record[key]) > 100
                   for key in _REQUIRED)):
        raise CurrentResultQueryError("invalid_response")
    if (not _YEAR.fullmatch(record["xn"])
            or not _TERM.fullmatch(record["xqM"])
            or not valid_current_account(record["userCode"])):
        raise CurrentResultQueryError("invalid_response")
    if not hmac.compare_digest(
        record["userCode"].encode("utf-8"), verified_user_code.encode("utf-8")
    ):
        raise CurrentResultQueryError("identity_mismatch")
    # Observed JS uses standard Base64; HTTPX will encode it as URL params.
    query = f'xn={record["xn"]}&xq={record["xqM"]}&xh={record["userCode"]}'
    return base64.b64encode(query.encode("ascii")).decode("ascii")
