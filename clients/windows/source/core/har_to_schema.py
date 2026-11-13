"""Offline HAR -> KingoClient protocol draft generator (read-only, draft-only).

This module fills the gap left by the deliberately *audit-only* HAR tools
(har_schema.py / har_audit.py): it maps a browser HAR to a **draft**
``protocol`` block that the read-only KingoClient (client.py) consumes, so the
operator no longer has to copy endpoints, search fields and HTML column indices
out of F12 by hand.

Privacy / safety contract (same bar as the rest of this product):
  * No network I/O, no replay, no decryption, no auth use.
  * Credentials, cookies, tokens, signatures and per-session tickets are redacted.
  * The output is ALWAYS a *draft*: ``protocol.verified`` is forced to ``False``
    and ``offline_selftest`` rejects any distributed template that claims
    verified endpoints. Every auto-filled field carries a warning; the entry
    page, identity page, table/empty markers and submit are intentionally left
    for manual verification because the HAR alone cannot prove them safe.
  * This is NOT a protocol/endpoint verifier. The official UI must still be
    checked against every emitted path before ``verified`` is flipped to True.

The research equivalent (kingo-live/har_to_schema.py) targets the separate
live_client.py JSON schema and is kept under tools/; this file targets the
shipped read-only client and its HTML-table ``protocol`` config.
"""
from __future__ import annotations

import argparse
import base64
import copy
import json
import re
import sys
import tempfile
import urllib.parse
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    import yaml  # PyYAML is in requirements.txt; fall back to JSON if missing.
except Exception:  # pragma: no cover - PyYAML is always installed for the app
    yaml = None


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------

SENSITIVE_NAMES = {
    "cookie", "set-cookie", "authorization", "proxy-authorization",
    "token", "access_token", "refresh_token", "jsessionid", "session",
    "sessionid", "sid", "phpsessid", "asp.net_sessionid", "password",
    "passwd", "pwd", "secret", "signature", "sign", "nonce", "ticket",
    "csrf", "csrf_token", "xsrf", "x-xsrf-token", "x-csrf-token",
    "x-api-key", "apikey", "api_key", "auth", "credential", "skey",
    "openid", "unionid", "studentid", "xh", "sno", "idcard", "phone",
}

SENSITIVE_HINTS = ("token", "cookie", "session", "secret", "password", "passwd",
                   "sign", "ticket", "auth", "credential", "key", "pwd", "sid")


def is_sensitive(name: str) -> bool:
    n = name.strip().lower().replace("-", "_")
    if n in {x.replace("-", "_") for x in SENSITIVE_NAMES}:
        return True
    return any(h in n for h in SENSITIVE_HINTS) and n not in ("keys", "keyword")


def redact(name: str, value: Any, strict: bool = False) -> str:
    s = "" if value is None else str(value)
    if is_sensitive(name) or strict:
        return f"<REDACTED:len={len(s)}>"
    return scrub(s[:120])


def scrub(text: str) -> str:
    """Remove phone numbers and ID-card numbers from any emitted text."""
    text = re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", "<PHONE>", text)
    text = re.sub(r"(?<!\d)\d{17}[\dXx](?!\d)", "<IDCARD>", text)
    return text


# ---------------------------------------------------------------------------
# HAR parsing
# ---------------------------------------------------------------------------

STATIC_EXT = (".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico",
              ".woff", ".woff2", ".ttf", ".map", ".webp", ".mp4", ".wasm")


def decode_content(entry: dict) -> Tuple[str, str]:
    resp = entry.get("response", {}) or {}
    content = resp.get("content", {}) or {}
    text = content.get("text", "") or ""
    mime = str(content.get("mimeType", "") or "")
    if content.get("encoding") == "base64" and text:
        try:
            text = base64.b64decode(text).decode("utf-8", errors="replace")
        except Exception:
            text = ""
    # Kingosoft / mobile endpoints often return literal \uXXXX JSON escapes.
    text = re.sub(r"\\u([0-9a-fA-F]{4})",
                  lambda m: chr(int(m.group(1), 16)), text)
    return text, mime


def entry_url(entry: dict) -> Tuple[str, str, str, List[Tuple[str, str]]]:
    url = str((entry.get("request", {}) or {}).get("url", "") or "")
    u = urllib.parse.urlsplit(url)
    q = urllib.parse.parse_qsl(u.query, keep_blank_values=True)
    return u.scheme, u.netloc, u.path or "/", q


def post_params(entry: dict) -> Tuple[Dict[str, str], str]:
    req = entry.get("request", {}) or {}
    pd = req.get("postData", {}) or {}
    params: Dict[str, str] = {}
    for p in pd.get("params", []) or []:
        if isinstance(p, dict) and "name" in p:
            params[str(p["name"])] = str(p.get("value", ""))
    text = str(pd.get("text", "") or "")
    if text and not params:
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                params = {str(k): str(v) for k, v in obj.items()}
        except (ValueError, TypeError):
            params = dict(urllib.parse.parse_qsl(text, keep_blank_values=True))
    mime = str(pd.get("mimeType", "") or "")
    return params, mime


@dataclass
class Candidate:
    idx: int
    method: str
    host: str
    path: str
    query: List[Tuple[str, str]]
    status: int
    mime: str
    params: Dict[str, str]
    post_mime: str
    body: str
    headers_req: Dict[str, str]
    headers_resp: Dict[str, str]
    scores: Dict[str, int] = field(default_factory=dict)
    reasons: Dict[str, List[str]] = field(default_factory=dict)
    list_path: str = ""
    row_keys: List[str] = field(default_factory=list)
    row_count: int = 0
    tags: List[str] = field(default_factory=list)

    def public(self, strict: bool = False) -> dict:
        return {
            "idx": self.idx,
            "method": self.method,
            "host": self.host,
            "path": self.path,
            "query": [[k, redact(k, v, strict)] for k, v in self.query],
            "status": self.status,
            "mime": self.mime,
            "post_mime": self.post_mime,
            "post_params": [[k, redact(k, v, strict)] for k, v in self.params.items()],
            "request_headers": {k: redact(k, v, strict)
                                for k, v in self.headers_req.items()},
            "response_headers": {k: redact(k, v, strict)
                                 for k, v in self.headers_resp.items()},
            "scores": self.scores,
            "reasons": self.reasons,
            "json_list_path": self.list_path,
            "row_keys": self.row_keys,
            "row_count": self.row_count,
            "tags": self.tags,
            "body_preview": ("<BODY:len=%d>" % len(self.body)) if strict
                            else scrub(self.body[:400]),
        }


def load_entries(har_path: str, host_filter: str = "") -> List[Candidate]:
    raw = json.loads(Path(har_path).read_text(encoding="utf-8-sig"))
    entries = (raw.get("log", {}) or {}).get("entries", []) or []
    out: List[Candidate] = []
    for i, e in enumerate(entries):
        req = e.get("request", {}) or {}
        resp = e.get("response", {}) or {}
        scheme, host, path, query = entry_url(e)
        if path.lower().endswith(STATIC_EXT):
            continue
        if host_filter and host_filter.lower() not in host.lower():
            continue
        params, pmime = post_params(e)
        body, mime = decode_content(e)
        hr = {str(h.get("name", "")): str(h.get("value", ""))
              for h in req.get("headers", []) or [] if isinstance(h, dict)}
        hs = {str(h.get("name", "")): str(h.get("value", ""))
              for h in resp.get("headers", []) or [] if isinstance(h, dict)}
        out.append(Candidate(
            idx=i, method=str(req.get("method", "GET")).upper(), host=host,
            path=path, query=query, status=int(resp.get("status", 0) or 0),
            mime=mime, params=params, post_mime=pmime, body=body,
            headers_req=hr, headers_resp=hs))
    return out


def guess_host(cands: List[Candidate]) -> str:
    counts = Counter(c.host for c in cands
                     if not c.path.lower().endswith(STATIC_EXT))
    if not counts:
        return ""
    api = Counter(c.host for c in cands
                  if "json" in c.mime.lower() or "json" in c.post_mime.lower()
                  or c.method == "POST")
    return (api or counts).most_common(1)[0][0]


# ---------------------------------------------------------------------------
# Shape inference
# ---------------------------------------------------------------------------

def find_list_path(obj: Any, prefix: str = "", depth: int = 0) -> Tuple[str, List[str], int]:
    if depth > 6:
        return "", [], 0
    if isinstance(obj, list):
        first = obj[0] if obj else None
        if isinstance(first, dict):
            return prefix, [str(k) for k in first.keys()], len(obj)
        if isinstance(first, list):
            return prefix, [str(i) for i in range(len(first))], len(obj)
        return "", [], 0
    if isinstance(obj, dict):
        best: Tuple[str, List[str], int] = ("", [], 0)
        for k, v in obj.items():
            p = f"{prefix}.{k}" if prefix else str(k)
            got = find_list_path(v, p, depth + 1)
            if got[0] and got[2] > best[2]:
                best = got
        return best
    return "", [], 0


def html_columns(body: str) -> Tuple[List[str], int]:
    heads = re.findall(r"<th[^>]*>(.*?)</th>", body, flags=re.I | re.S)
    clean = [re.sub(r"<[^>]+>", "", h).strip() for h in heads]
    rows = len(re.findall(r"<tr[^>]*>", body, flags=re.I))
    return [c for c in clean if c], max(0, rows - 1)


# Order matters: more specific keys/hints first so Chinese headers such as
# 课号 / 课程名称 disambiguate from 课程. Real 青果/Kingosoft pages use Chinese
# <th> text, so Chinese hints are required for automatic column mapping.
COLUMN_HINTS: Dict[str, List[str]] = {
    "course_id": ["kcid", "kch", "courseid", "course_id", "courseno", "kcdm",
                  "kcbm", "kcbh", "coursecode",
                  "课号", "课程号", "课程代码", "课程编码", "编号", "代码"],
    "class_id": ["skbj", "jxbh", "classid", "class_id", "teachingclassid",
                 "teachclass", "bjdm", "xkbh", "jxbid", "classno", "skbh",
                 "教学班", "班级", "教学班号", "班次", "课序号"],
    "course_name": ["kcmc", "kcm", "coursename", "course_name", "coursetitle",
                   "name",
                   "课程名称", "课程名", "课名", "名称"],
    "teacher": ["jsxm", "teacher", "teachername", "xm", "teacher_name",
                "教师", "老师", "任课教师", "授课教师"],
    "remain": ["kyl", "remain", "syyl", "surplus", "available", "kcyl",
               "yl", "left", "rest", "capacity", "yuliang", "kyrs",
               "余量", "剩余", "余", "剩余名额", "容量", "选余", "剩余数"],
}


def guess_columns(keys: List[str]) -> Tuple[Dict[str, str], List[str]]:
    low = {str(k).lower().replace("_", "").replace("-", ""): str(k) for k in keys}
    cols: Dict[str, str] = {}
    notes: List[str] = []
    for role, hints in COLUMN_HINTS.items():
        hit = None
        for h in hints:
            if h in low:
                hit = low[h]
                break
        if hit:
            cols[role] = hit
            notes.append(f"{role} <- '{hit}' (exact key)")
    if not cols:
        notes.append("no known column keys; fill protocol.columns manually")
    return cols, notes


# timetable endpoints are NOT a selectable-course catalog: the single most
# common mis-mapping in NCWU / Kingosoft captures.
NOT_CATALOG = re.compile(
    r"mycourseschedule|course_schedule|timetable|schedule|kebiao|/kb\b|"
    r"calendar|week1|sjhjinfo", re.I)
CATALOG_URL = re.compile(
    r"catalog|course/list|courselist|kcxx|kclb|kccx|selectable|"
    r"xuanke|xk/list|/list\b|query|search|/kc/|course/query|"
    r"available|kecheng|choose", re.I)
ENTRY_URL = re.compile(r"wsxk|xuanke|elective|entry|main|index|/xsxk|/xsxk/", re.I)
BODY_FIELD_WORDS = re.compile(
    r"kcid|skbj|jxbh|kch|jxbid|xnxq|kcmc|kyl|remain|courseid|classid", re.I)
FULL_MARKERS = ["已满", "名额已满", "无余量", "人数已满", "选课人数已满",
                "no seat", "full", "no seats", "capacity"]
WINDOW_MARKERS = ["不是有效的选课时间", "未开放", "尚未开始", "不在选课时间",
                  "选课时间", "not in selection time", "not open"]
TERM_WORDS = re.compile(r"xnxq|term|xqdm|\bxq\b|学年|学期", re.I)


def score_all(cands: List[Candidate]) -> None:
    for c in cands:
        body_low = c.body[:20000].lower()
        url = c.path + "?" + urllib.parse.urlencode(c.query)
        keys_blob = " ".join(c.row_keys + list(c.params.keys())).lower()
        s = {"catalog": 0, "submit": 0, "enrollment": 0}
        r: Dict[str, List[str]] = {"catalog": [], "submit": [], "enrollment": []}

        lp, keys, n = find_list_path(_safe_json(c.body))
        if lp:
            c.list_path, c.row_keys, c.row_count = lp, keys, n
        if "json" in c.mime.lower():
            try:
                json.loads(c.body)
            except Exception:
                pass
        heads, nrows = html_columns(c.body)
        if heads:
            c.row_keys, c.row_count = heads, nrows
            c.tags.append("html_table")

        if c.row_count > 0 and c.row_keys:
            s["catalog"] += 6
            r["catalog"].append(f"{c.row_count} rows with keys {c.row_keys[:6]}")
            s["enrollment"] += 3
        if BODY_FIELD_WORDS.search(keys_blob):
            s["catalog"] += 4
            r["catalog"].append("course/class field names present")
        if re.search(r"kyl|remain|surplus|余量|ky", keys_blob, re.I):
            s["catalog"] += 3
            r["catalog"].append("seat-remaining column present")

        if CATALOG_URL.search(url):
            s["catalog"] += 3
            r["catalog"].append("URL looks like a course query")
        if c.method == "POST":
            s["submit"] += 3
            r["submit"].append("POST")
        else:
            s["catalog"] += 1
            s["enrollment"] += 1
        if c.params:
            s["submit"] += 2
            r["submit"].append(f"has postData ({len(c.params)} fields)")
        if any(TERM_WORDS.search(k) for k in list(c.params) + [k for k, _ in c.query]):
            s["catalog"] += 1
            s["submit"] += 1

        for m in FULL_MARKERS:
            if m.lower() in body_low:
                s["submit"] += 2
                c.tags.append("full_marker:" + m)
                break
        for m in WINDOW_MARKERS:
            if m.lower() in body_low:
                s["submit"] += 2
                c.tags.append("window_marker:" + m)
                break
        if re.search(r'"prohibit"\s*:\s*"1"|当前时间不是有效的选课时间', c.body, re.I):
            c.tags.append("window_closed_evidence")
            s["catalog"] -= 2
        if any(m.lower() in body_low for m in ("选课成功", "成功", "success")):
            s["submit"] += 1

        if NOT_CATALOG.search(url) or re.search(r"sjhjinfo|week1", body_low):
            c.tags.append("NOT_CATALOG:timetable_or_schedule")
            s["catalog"] = 0
            r["catalog"].append("timetable/schedule is NOT a selectable-course catalog")
        if c.status >= 400 or c.status == 0:
            for k in s:
                s[k] = max(0, s[k] - 3)
        for k in ("catalog", "submit", "enrollment"):
            s[k] = max(0, s[k])
        c.scores, c.reasons = s, r


def _safe_json(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Protocol draft construction (product client.py schema)
# ---------------------------------------------------------------------------

# The shipped read-only client reads an HTML table; column indices are the
# position of each <td> in a row. These are the only roles client._column_map
# accepts.
PRODUCT_COLUMN_ROLES = ("course_id", "course_name", "teacher", "remain", "class_id")
PRODUCT_REQUIRED = ("course_id", "course_name", "teacher", "remain")


def choose(cands: List[Candidate], role: str, picks: Dict[str, int],
           *, prefer_html: bool = False) -> Optional[Candidate]:
    if role in picks:
        for c in cands:
            if c.idx == picks[role]:
                return c
        return None
    pool = [c for c in cands if c.scores.get(role, 0) > 0
            and "NOT_CATALOG:timetable_or_schedule" not in c.tags]
    if not pool:
        return None
    if prefer_html:
        html_pool = [c for c in pool if "html_table" in c.tags]
        if html_pool:
            pool = html_pool
    return sorted(pool, key=lambda c: (-c.scores[role], c.idx))[0]


def path_only(c: Candidate) -> str:
    """Product protocol stores the path only; search params go to list_fields."""
    return c.path


def derive_list_fields(cat: Candidate, strict: bool,
                       warnings: List[str]) -> Tuple[str, Dict[str, str]]:
    """Split the catalog request params into term_field + static list_fields."""
    params: Dict[str, str] = {}
    for k, v in cat.query:
        params[str(k)] = str(v)
    params.update({str(k): str(v) for k, v in cat.params.items()})

    term_keys = [k for k in params if TERM_WORDS.search(k)]
    term_field = term_keys[0] if term_keys else ""
    if not term_field:
        warnings.append("no term field (xnxq/term) found in catalog request -> "
                        "set protocol.term_field manually")

    list_fields: Dict[str, str] = {}
    for k, v in params.items():
        if k == term_field:
            continue
        if is_sensitive(k):
            warnings.append(f"catalog param '{k}' looks sensitive -> excluded "
                            f"from list_fields; add manually if the site needs it")
            continue
        list_fields[k] = redact(k, v, strict) if strict else v
    return term_field, list_fields


def derive_columns(cat: Candidate, warnings: List[str]) -> Dict[str, int]:
    """Map HTML <th> headers to 0-based column indices for client._column_map."""
    if "html_table" not in cat.tags:
        warnings.append("catalog response is JSON, not an HTML table; the "
                        "read-only client requires HTML columns -> set "
                        "protocol.columns manually from the real page")
        return {}
    cols: Dict[str, int] = {}
    for i, header in enumerate(cat.row_keys):
        norm = header.lower().replace("_", "").replace("-", "")
        role = None
        for cand, hints in COLUMN_HINTS.items():
            if cand not in PRODUCT_COLUMN_ROLES:
                continue
            if any(h in norm for h in hints):
                role = cand
                break
        if role:
            cols[role] = i
    missing = [r for r in PRODUCT_REQUIRED if r not in cols]
    if missing:
        warnings.append("could not infer HTML column(s) " + ", ".join(missing)
                        + " from <th> headers -> set protocol.columns manually")
    return cols


def choose_entry(cands: List[Candidate], cat: Optional[Candidate]) -> Tuple[str, List[str]]:
    warnings: List[str] = []
    for c in cands:
        if c is cat:
            continue
        if c.method == "GET" and c.mime.lower().startswith("text/html") \
                and ENTRY_URL.search(c.path) \
                and "NOT_CATALOG:timetable_or_schedule" not in c.tags:
            return c.path, warnings
    if cat is not None:
        warnings.append("no distinct entry/landing page detected; entry defaults "
                        "to the catalog path. Verify against the official UI "
                        "(the entry page often differs in 青果/Kingosoft).")
        return path_only(cat), warnings
    return "", warnings


def build_protocol_draft(cands: List[Candidate], picks: Dict[str, int],
                         strict: bool = False) -> Tuple[dict, dict]:
    cat = choose(cands, "catalog", picks, prefer_html=True)
    warnings: List[str] = []

    draft: dict = {
        "verified": False,
        "identity_path": "",
        "identity_referer": "/",
        "identity_selector": "",
        "entry": "",
        "entry_referer": "/",
        "list": "",
        "list_method": "GET",
        "term_field": "",
        "list_fields": {},
        "form_selector": "form",
        "checkbox_selector": 'input[type="checkbox"]',
        "table_marker": "",
        "empty_marker": "",
        "columns": {},
    }

    if cat is None:
        warnings.append("no catalog candidate found -> nothing auto-filled; "
                        "set protocol.list and protocol.columns manually")
        report = _make_report(cands, picks, strict, warnings, {}, "")
        return draft, report

    is_html = "html_table" in cat.tags
    draft["list"] = path_only(cat)
    draft["list_method"] = cat.method
    draft["entry"], entry_warn = choose_entry(cands, cat)
    warnings.extend(entry_warn)

    term_field, list_fields = derive_list_fields(cat, strict, warnings)
    draft["term_field"] = term_field
    draft["list_fields"] = list_fields

    columns = derive_columns(cat, warnings)
    draft["columns"] = columns

    warnings.append("identity_path / identity_selector are NOT auto-detected "
                    "(the HAR cannot prove which page shows your own name). "
                    "Set them from the page that displays your name; "
                    "protocol.verified must stay False until you verify there.")
    warnings.append("table_marker / empty_marker are NOT auto-detected. Add them "
                    "from the real catalog page to let the client fail closed on "
                    "unexpected pages / empty states.")
    warnings.append("this is a DRAFT. Flip protocol.verified to True only after "
                    "checking every path and column against the official UI.")

    for c in cands:
        if "NOT_CATALOG:timetable_or_schedule" in c.tags:
            warnings.append(f"entry #{c.idx} {c.path} is a TIMETABLE/schedule "
                            "response; it is not a selectable-course catalog and "
                            "was excluded from protocol.list")
        if "window_closed_evidence" in c.tags:
            warnings.append(f"entry #{c.idx} shows prohibit/window-closed text; "
                            "the capture was made outside the selection window")

    report = _make_report(cands, picks, strict, warnings,
                          {"html_catalog": is_html}, cat.path)
    return draft, report


def _make_report(cands, picks, strict, warnings, extra, catalog_path) -> dict:
    return {
        "kind": "har_protocol_report",
        "source": "browser HAR (redacted)",
        "strict": strict,
        "entry_count": len(cands),
        "picks": picks,
        "catalog_path": catalog_path,
        "warnings": warnings,
        "extra": extra,
        "credential_note": ("Cookie / Authorization / token values are replaced "
                            "with <REDACTED:len=N>. Do not paste raw HAR into chat; "
                            "pass the local file path instead."),
        "candidates": [c.public(strict) for c in cands],
    }


def merge_protocol(cfg: dict, draft: dict) -> dict:
    """Return a copy of cfg with protocol draft merged; verified forced False."""
    out = copy.deepcopy(cfg)
    protocol = copy.deepcopy(out.get("protocol", {}))
    # Keep static defaults the draft did not touch (form_selector etc.) and
    # overlay only the auto-filled fields.
    for key, value in draft.items():
        if key == "verified":
            continue
        protocol[key] = value
    protocol["verified"] = False
    out["protocol"] = protocol
    return out


def dump_config(block: dict, path: Path) -> None:
    if yaml is not None:
        path.write_text(yaml.safe_dump(block, allow_unicode=True,
                                        sort_keys=False), encoding="utf-8")
    else:  # pragma: no cover
        path.write_text(json.dumps(block, ensure_ascii=False, indent=2),
                        encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def print_table(cands: List[Candidate], picks: Dict[str, int]) -> None:
    print(f"{'#':>4} {'method':<7} {'status':>6} {'cat':>4}  path")
    print("-" * 90)
    for c in cands:
        mark = "*" if picks.get("catalog") == c.idx else " "
        print(f"{c.idx:>4} {c.method:<7} {c.status:>6} "
              f"{c.scores.get('catalog', 0):>4} {mark} {c.host}{c.path[:60]}")
    print("-" * 90)
    print("  * = auto-selected catalog role. Override with --pick catalog=N")


def parse_picks(items: List[str]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for it in items:
        if "=" not in it:
            continue
        k, v = it.split("=", 1)
        if k in ("catalog", "submit", "enrollment") and v.isdigit():
            out[k] = int(v)
    return out


def run(args: argparse.Namespace) -> int:
    cands = load_entries(args.har, args.host or "")
    if not args.host:
        host = guess_host(cands)
        if host:
            cands = [c for c in cands if c.host == host]
    if not cands:
        print("No usable API entries in HAR (static resources filtered).",
              file=sys.stderr)
        return 2
    score_all(cands)
    picks = parse_picks(args.pick)
    draft, report = build_protocol_draft(cands, picks, strict=args.strict)

    if args.list:
        print_table(cands, picks)
        return 0

    print(f"HAR entries (filtered): {len(cands)} | host(s): "
          f"{', '.join(sorted({c.host for c in cands}))}")
    print_table(cands, picks)
    print(f"\nAuto-selected catalog: "
          f"{'#' + str(picks.get('catalog')) if 'catalog' in picks else 'see report'}")
    print(f"protocol.list = {draft['list']} ({draft['list_method']})")
    print(f"protocol.term_field = {draft['term_field'] or '(unset)'}")
    print(f"protocol.list_fields = {draft['list_fields']}")
    print(f"protocol.columns = {draft['columns']}")
    if report["warnings"]:
        print("\nWARNINGS (manual verification required):")
        for w in report["warnings"]:
            print("  ! " + w)

    dump_config(draft, Path(args.out))
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    print(f"\nWrote {args.out} (protocol DRAFT, verified=false) "
          f"and {args.report} (redacted report)")

    if args.merge:
        cfg = yaml.safe_load(Path(args.merge).read_text(encoding="utf-8")) \
            if Path(args.merge).suffix in (".yaml", ".yml") and yaml is not None \
            else json.loads(Path(args.merge).read_text(encoding="utf-8"))
        merged = merge_protocol(cfg, draft)
        dest = args.out_merged or "config.draft.yaml"
        dump_config(merged, Path(dest))
        print(f"Wrote {dest} (merge of {args.merge} + draft; verified forced False)")
    print("\nNext: open protocol-draft.yaml, verify every path/column against the "
          "official UI, then flip protocol.verified to True.")
    return 0


def self_test() -> int:
    def entry(i, method, path, params=None, body="", status=200,
              mime="text/html; charset=GBK", resp_headers=None):
        return {
            "request": {"method": method,
                        "url": "https://jw.example.edu.cn" + path,
                        "headers": [{"name": "Cookie", "value": "JSESSIONID=SECRET123"},
                                    {"name": "Accept", "value": "text/html"}],
                        "queryString": [],
                        "postData": {"mimeType": "application/x-www-form-urlencoded",
                                     "params": [{"name": k, "value": v}
                                                for k, v in (params or {}).items()]}},
            "response": {"status": status,
                         "headers": [{"name": "Content-Type", "value": mime}]
                                    + [{"name": k, "value": v}
                                       for k, v in (resp_headers or {}).items()],
                         "content": {"mimeType": mime, "text": body, "size": len(body)}},
        }

    # An HTML course-list page (青果 style) with a query carrying xnxq + a
    # static college code. <th> headers reveal the column order.
    th = ("<th>课号</th><th>课程名称</th><th>教师</th><th>余量</th>"
          "<th>教学班</th>")
    rows = "".join(
        f"<tr><td>K00{n}</td><td>课程{n}</td><td>老师{n}</td>"
        f"<td>{3 - n}</td><td>0{n}</td></tr>" for n in range(2))
    catalog_body = f"<table>{th}{rows}</table>"
    har = {"log": {"entries": [
        entry(0, "GET", "/js/app.js", body="var x=1;", mime="application/javascript"),
        entry(1, "GET", "/xsxk/courseList",
              params={"xnxq": "2026-2027-1", "kkyx": "01"},
              body=catalog_body),
        entry(2, "GET", "/hsjw/wap/mycourseschedule.action",
              body=json.dumps({"sjhjinfo": [], "prohibit": "1",
                               "msg": "当前时间不是有效的选课时间区段!"},
                              ensure_ascii=True)),
    ]}}

    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "sample.har"
        p.write_text(json.dumps(har, ensure_ascii=False), encoding="utf-8")
        cands = load_entries(str(p))
        cands = [c for c in cands if c.host == guess_host(cands)]
        score_all(cands)
        picks: Dict[str, int] = {}
        draft, report = build_protocol_draft(cands, picks, strict=False)
        report_text = json.dumps(report, ensure_ascii=False)

        checks = [
            ("static resources filtered", len(cands) == 2, f"len={len(cands)}"),
            ("catalog identified (html table)", report["catalog_path"]
             == "/xsxk/courseList", str(report["catalog_path"])),
            ("list path only (no query)", draft["list"] == "/xsxk/courseList",
             draft["list"]),
            ("list_method GET", draft["list_method"] == "GET", draft["list_method"]),
            ("term_field inferred", draft["term_field"] == "xnxq",
             draft["term_field"]),
            ("list_fields excludes term + sensitive",
             draft["list_fields"] == {"kkyx": "01"}
             and "xnxq" not in draft["list_fields"], str(draft["list_fields"])),
            ("columns mapped to html indices",
             draft["columns"].get("course_id") == 0
             and draft["columns"].get("course_name") == 1
             and draft["columns"].get("teacher") == 2
             and draft["columns"].get("remain") == 3
             and draft["columns"].get("class_id") == 4,
             str(draft["columns"])),
            ("verified forced False", draft["verified"] is False, str(draft["verified"])),
            ("cookie redacted in report",
             "SECRET123" not in report_text, "secret leak"),
            ("timetable excluded from catalog",
             any("TIMETABLE" in w or "timetable" in w.lower() for w in report["warnings"]),
             str(report["warnings"])),
            ("identity left empty + warned",
             draft["identity_path"] == ""
             and any("identity" in w for w in report["warnings"]),
             str(draft["identity_path"])),
            ("manual verification warning present",
             any("DRAFT" in w or "verified" in w.lower() for w in report["warnings"]),
             str(report["warnings"])),
        ]
        cfg = {"protocol": {"verified": False, "list": "/OLD", "columns": {}},
               "courses": [{"course_id": "KEEP"}]}
        merged = merge_protocol(cfg, draft)
        checks.append((
            "merge_protocol preserves other keys + forces verified False",
            merged["courses"] == [{"course_id": "KEEP"}]
            and merged["protocol"]["list"] == "/xsxk/courseList"
            and merged["protocol"]["verified"] is False,
            str(merged.get("courses"))))

        bad = 0
        for label, ok, detail in checks:
            print(f"  [{'PASS' if ok else 'FAIL'}] {label}"
                  + ("" if ok else f" -> {detail}"))
            bad += 0 if ok else 1
        print(f"\n{len(checks) - bad}/{len(checks)} checks passed")
        return 1 if bad else 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Offline HAR -> KingoClient protocol draft (read-only, draft-only)")
    ap.add_argument("har", nargs="?", help="path to .har file")
    ap.add_argument("--host", default="", help="only keep this host substring")
    ap.add_argument("--pick", action="append", default=[], metavar="ROLE=IDX",
                    help="override auto-selection, e.g. --pick catalog=3")
    ap.add_argument("--list", action="store_true", help="only print candidate table")
    ap.add_argument("--strict", action="store_true",
                    help="redact ALL business values in the report")
    ap.add_argument("--out", default="protocol-draft.yaml")
    ap.add_argument("--report", default="har-report.json")
    ap.add_argument("--merge", metavar="CONFIG", help="merge draft into this config")
    ap.add_argument("--out-merged", default="")
    ap.add_argument("--self-test", action="store_true")
    args = ap.parse_args(argv)
    if args.self_test:
        return self_test()
    if not args.har:
        ap.error("HAR path required (or use --self-test)")
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
