"""Privacy-preserving, offline JSON response-shape signals.

Adapted from shape-discovery ideas in 07xh/kingo-live/har_to_schema.py.
This is *not* a protocol, endpoint, capacity or access-policy verifier.
No request replay, path discovery, write mapping, raw payload export or auth use.
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

MAX_SHAPE_DEPTH = 6
MAX_SHAPE_TEXT_BYTES = 1024 * 1024

# Exact field-key hints only. The presence of a key does NOT establish its meaning.
_HINTS = {
    "course_id": frozenset(("kcid", "courseid", "kcdm", "kch")),
    "class_id": frozenset(("skbj", "jxbh", "jxbid", "classid", "teachingclassid")),
    "remaining": frozenset(("kyl", "remain", "remaining", "surplus", "syrl", "availablecount")),
}
_TIMETABLE_PATH = re.compile(
    r"mycourseschedule|course[_-]?schedule|timetable|kebiao|/kb(?:/|$)", re.I
)
_TIMETABLE_RESPONSE_KEYS = frozenset(("sjhjinfo", "week1", "week2", "week3",
                                      "week4", "week5", "week6", "week7"))
_CLOSED_TEXT = "当前时间不是有效的选课时间区段"


def _normalize(key: Any) -> str:
    if not isinstance(key, str):
        return ""
    # Never emit key text; normalize for an exact allowlist match only.
    return key.casefold().replace("_", "").replace("-", "")


def _rows(value: Any, depth: int = 0) -> list[dict] | None:
    """Locate one nested non-empty JSON object-array, with bounded traversal."""
    if depth > MAX_SHAPE_DEPTH:
        return None
    if isinstance(value, list):
        if value and all(isinstance(row, dict) for row in value[:8]):
            return value[:8]
        return None
    if isinstance(value, dict):
        for item in value.values():
            result = _rows(item, depth + 1)
            if result:
                return result
    return None


def inspect_offline_readonly_shape(path: str, method: str, response: Mapping[str, Any]) -> str:
    """Return a fixed non-sensitive category for an authorized local HAR entry.

    Only a GET/200 JSON response may produce a *candidate*; this will never
    mark a live endpoint as verified and must not mutate runtime configuration.
    Return values contain no source URL, field names, account or response text.
    """
    if not isinstance(path, str) or not isinstance(response, Mapping):
        return "not_applicable"
    if _TIMETABLE_PATH.search(path):
        return "timetable_excluded"
    if method != "GET" or type(response.get("status")) is not int or response["status"] != 200:
        return "not_applicable"
    content = response.get("content")
    if not isinstance(content, dict) or content.get("encoding") not in (None, ""):
        return "not_applicable"
    mime = content.get("mimeType", "")
    if not isinstance(mime, str) or not (mime.lower().split(";", 1)[0].strip() == "application/json"
                                         or mime.lower().split(";", 1)[0].strip().endswith("+json")):
        return "not_applicable"
    raw = content.get("text")
    if not isinstance(raw, str) or len(raw) > MAX_SHAPE_TEXT_BYTES:
        return "not_applicable"
    if _CLOSED_TEXT in raw:
        return "window_closed_excluded"
    try:
        data = json.loads(raw)
    except (ValueError, TypeError, RecursionError):
        return "not_applicable"
    if isinstance(data, dict) and (data.get("prohibit") == "1" or data.get("prohibit") == 1):
        return "window_closed_excluded"
    if isinstance(data, dict) and _TIMETABLE_RESPONSE_KEYS & set(data):
        return "timetable_excluded"
    rows = _rows(data)
    if not rows:
        return "not_applicable"
    for row in rows:
        if _TIMETABLE_RESPONSE_KEYS & set(row):
            return "timetable_excluded"
    keys = {_normalize(key) for row in rows for key in row}
    roles = {role for role, candidates in _HINTS.items() if keys & candidates}
    if roles == {"course_id", "class_id", "remaining"}:
        return "candidate_requires_manual_review"
    if "course_id" in roles or "class_id" in roles:
        return "fields_incomplete"
    return "not_applicable"