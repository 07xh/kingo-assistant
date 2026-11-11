"""Offline structural comparison only; never verifies a live school catalog."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
import argparse
import json
import re
from typing import Any

MAX_FILE_BYTES = 128 * 1024
_ALLOWED_ROW = frozenset({"key", "remaining"})
_SENSITIVE = re.compile(
    r"cookie|token|password|passwd|secret|authorization|session|signature|"
    r"sign(?:ature)?|(^|_)param[0-9]*($|_)|student|stu_id|student_id|"
    r"course_name|teacher|real_name|phone|email|raw_?(?:body|headers|response)",
    re.IGNORECASE,
)


def _has_sensitive_keys(value: Any) -> bool:
    if isinstance(value, dict):
        return any(_SENSITIVE.search(str(k)) or _has_sensitive_keys(v)
                   for k, v in value.items())
    if isinstance(value, list):
        return any(_has_sensitive_keys(v) for v in value)
    return False


def _valid_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or len(value) > 45:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return None
        return parsed
    except ValueError:
        return None


def _row_map(value: Any) -> dict[str, int] | None:
    if not isinstance(value, list) or not 2 <= len(value) <= 30:
        return None
    result: dict[str, int] = {}
    for row in value:
        if not isinstance(row, dict) or set(row) != _ALLOWED_ROW:
            return None
        key, remaining = row["key"], row["remaining"]
        if (not isinstance(key, str) or not re.fullmatch(r"[A-Z][A-Z0-9_-]{0,19}", key)
                or type(remaining) is not int or not 0 <= remaining <= 9999
                or key in result):
            return None
        result[key] = remaining
    return result


def assess_catalog_evidence(evidence: Any) -> dict[str, Any]:
    """Only assess redacted data consistency; never set a verified API flag."""
    result: dict[str, Any] = {
        "status": "blocked", "manual_review_required": True,
        "matched_rows": 0, "reason_codes": [],
    }
    if not isinstance(evidence, dict) or _has_sensitive_keys(evidence):
        result["reason_codes"].append("UNSAFE_OR_INVALID_DOCUMENT")
        return result
    if evidence.get("version") != 1:
        result["reason_codes"].append("UNSUPPORTED_VERSION")
        return result
    if evidence.get("source") != "official_readonly_catalog":
        result["reason_codes"].append("UNCONFIRMED_SOURCE")
        return result
    if evidence.get("official_selection_window_open") is not True:
        result["reason_codes"].append("SELECTION_WINDOW_NOT_CONFIRMED")
        return result
    if evidence.get("official_ui_checked_by_user") is not True:
        result["reason_codes"].append("OFFICIAL_UI_NOT_CONFIRMED")
        return result
    field = evidence.get("candidate_remaining_field")
    if (not isinstance(field, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,59}", field)
            or field.lower() in {"yixuan", "kex", "kcid", "skbj", "kcmc", "zxinfo", "zxxq"}):
        result["reason_codes"].append("REMAINING_FIELD_NOT_ESTABLISHED")
        return result
    ui, api = evidence.get("ui"), evidence.get("response")
    if not isinstance(ui, dict) or not isinstance(api, dict):
        result["reason_codes"].append("EVIDENCE_PAIR_MISSING")
        return result
    t1, t2 = _valid_time(ui.get("observed_at")), _valid_time(api.get("observed_at"))
    if t1 is None or t2 is None or abs((t1 - t2).total_seconds()) > 90:
        result["reason_codes"].append("TIMESTAMPS_TOO_FAR_APART_OR_INVALID")
        return result
    ui_rows, api_rows = _row_map(ui.get("rows")), _row_map(api.get("rows"))
    if ui_rows is None or api_rows is None or set(ui_rows) != set(api_rows):
        result["reason_codes"].append("ROW_MATCHING_UNDETERMINED")
        return result
    result["matched_rows"] = sum(ui_rows[k] == api_rows[k] for k in ui_rows)
    if result["matched_rows"] != len(ui_rows):
        result["reason_codes"].append("OBSERVATIONS_DISAGREE")
        return result
    if not any(n == 0 for n in ui_rows.values()) or not any(n > 0 for n in ui_rows.values()):
        result["status"] = "needs_more_evidence"
        result["reason_codes"].append("REQUIRE_BOTH_FULL_AND_AVAILABLE_SAMPLES")
        return result
    result["status"] = "ready_for_manual_review"
    result["reason_codes"].append("STRUCTURE_MATCH_ONLY_NOT_API_VERIFICATION")
    return result


def inspect_local_file(path: Path) -> dict[str, Any]:
    path = Path(path)
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
        return {"status": "blocked", "manual_review_required": True,
                "matched_rows": 0, "reason_codes": ["FILE_UNSAFE_OR_TOO_LARGE"]}
    try:
        with path.open("r", encoding="utf-8") as stream:
            data = json.load(stream)
    except (UnicodeError, json.JSONDecodeError, OSError):
        return {"status": "blocked", "manual_review_required": True,
                "matched_rows": 0, "reason_codes": ["INVALID_JSON"]}
    return assess_catalog_evidence(data)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Offline redacted catalog evidence assessment; no network requests")
    parser.add_argument("evidence", type=Path, help="Local redacted metadata only, NOT a HAR")
    outcome = inspect_local_file(parser.parse_args().evidence)
    print(json.dumps(outcome, ensure_ascii=False, sort_keys=True))
    return 0 if outcome["status"] == "ready_for_manual_review" else 1


if __name__ == "__main__":
    raise SystemExit(main())
