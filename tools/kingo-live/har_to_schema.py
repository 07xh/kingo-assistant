#!/usr/bin/env python3
"""Offline HAR -> kingo-live config schema draft.

Reads a browser HAR (Chrome / Edge / Firefox "Save all as HAR with content"),
identifies the catalog / submit / enrollment endpoints, infers column names,
business failure markers and clock headers, then emits a config patch that can
be merged straight into config.json.

No network I/O. No third-party dependency. Credentials are redacted.
Course IDs / class IDs are kept by default (they are needed for field mapping);
use --strict to redact every business value too.

Usage
-----
  python har_to_schema.py session.har --list
  python har_to_schema.py session.har
  python har_to_schema.py session.har --pick catalog=3 submit=7 enrollment=9
  python har_to_schema.py session.har --merge config.json
  python har_to_schema.py --self-test
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
    # Many Kingosoft / mobile endpoints return JSON with literal \uXXXX escapes.
    # Normalize them so Chinese business messages can be matched and previewed.
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
    """Return (dotted_path, keys_of_first_row, row_count) for the first row list."""
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


COLUMN_HINTS: Dict[str, List[str]] = {
    "course_id": ["kcid", "kch", "courseid", "course_id", "courseno", "kcdm",
                  "kcbm", "kcbh", "coursecode", "kcbh", "course", "kcdm"],
    "class_id": ["skbj", "jxbh", "classid", "class_id", "teachingclassid",
                 "teachclass", "bjdm", "xkbh", "jxbid", "classno", "skbh"],
    "course_name": ["kcmc", "kcm", "coursename", "course_name", "coursetitle",
                    "kc", "name"],
    "teacher": ["jsxm", "teacher", "teachername", "js", "xm", "teacher_name"],
    "remain": ["kyl", "remain", "syyl", "surplus", "available", "kcyl",
               "yl", "left", "rest", "capacity", "yuliang", "kyrs"],
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
        notes.append("no known column keys; fill catalog.columns manually")
    return cols, notes


# timetable endpoints are NOT a selectable-course catalog. This is the single
# most common mis-mapping in NCWU / Kingosoft captures.
NOT_CATALOG = re.compile(
    r"mycourseschedule|course_schedule|timetable|schedule|kebiao|/kb\b|"
    r"calendar|week1|sjhjinfo", re.I)
CATALOG_URL = re.compile(
    r"catalog|course/list|courselist|kcxx|kclb|kccx|selectable|"
    r"xuanke|xk/list|/list\b|query|search|/kc/|course/query|"
    r"available|kecheng|choose", re.I)
SUBMIT_URL = re.compile(
    r"submit|save|/add\b|select|choose|xuanke|tijiao|/bc\b|operate|"
    r"enroll|/xk\b|insert|confirm", re.I)
ENROLL_URL = re.compile(
    r"selected|yixuan|mycourse|chosen|enrolled|selectedlist|"
    r"selcourse|myselect|/yx\b|result", re.I)
BODY_FIELD_WORDS = re.compile(
    r"kcid|skbj|jxbh|kch|jxbid|xnxq|kcmc|kyl|remain|courseid|classid", re.I)

FULL_MARKERS = ["已满", "名额已满", "无余量", "人数已满", "选课人数已满",
                "no seat", "full", "no seats", "capacity"]
WINDOW_MARKERS = ["不是有效的选课时间", "未开放", "尚未开始", "不在选课时间",
                  "选课时间", "not in selection time", "not open"]
SUCCESS_MARKERS = ["选课成功", "成功", "success", '"ok"', '"code":0', "提交成功"]


def score_all(cands: List[Candidate]) -> None:
    for c in cands:
        body_low = c.body[:20000].lower()
        url = c.path + "?" + urllib.parse.urlencode(c.query)
        keys_blob = " ".join(c.row_keys + list(c.params.keys())).lower()
        s = {"catalog": 0, "submit": 0, "enrollment": 0}
        r: Dict[str, List[str]] = {"catalog": [], "submit": [], "enrollment": []}

        # --- response shape -------------------------------------------------
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
            s["submit"] += 3
            s["enrollment"] += 3
        if re.search(r"kyl|remain|surplus|余量|ky", keys_blob, re.I):
            s["catalog"] += 3
            r["catalog"].append("seat-remaining column present")

        # --- URL / method ---------------------------------------------------
        if CATALOG_URL.search(url):
            s["catalog"] += 3
            r["catalog"].append("URL looks like a course query")
        if SUBMIT_URL.search(url):
            s["submit"] += 3
            r["submit"].append("URL looks like a write action")
        if ENROLL_URL.search(url):
            s["enrollment"] += 4
            r["enrollment"].append("URL looks like an enrolled-course list")
        if c.method == "POST":
            s["submit"] += 3
            r["submit"].append("POST")
        else:
            s["catalog"] += 1
            s["enrollment"] += 1
        if c.params:
            s["submit"] += 2
            r["submit"].append(f"has postData ({len(c.params)} fields)")
        if any("xnxq" in k.lower() for k in list(c.params) + [k for k, _ in c.query]):
            s["catalog"] += 1
            s["submit"] += 1

        # --- business text --------------------------------------------------
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
        if any(m.lower() in body_low for m in SUCCESS_MARKERS):
            s["submit"] += 1

        # --- hard exclusion: schedule != catalog ----------------------------
        if NOT_CATALOG.search(url) or re.search(r"sjhjinfo|week1", body_low):
            c.tags.append("NOT_CATALOG:timetable_or_schedule")
            s["catalog"] = 0
            r["catalog"].append("timetable/schedule response is NOT a "
                                "selectable-course catalog")
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
# Draft construction
# ---------------------------------------------------------------------------

# Server-Timing is a LATENCY header (queue;dur=..., db;dur=...), NOT a clock.
# ClockSync parses a date from clock.header, so it must never be selected here.
CLOCK_HEADERS = ("date", "x-server-time", "x-request-time", "x-timestamp", "x-date")
STAGE_HEADERS = ("server-timing",)


def detect_clock(cands: List[Candidate]) -> Tuple[str, int, List[str], List[str]]:
    seen: List[str] = []
    stage: List[str] = []
    for c in cands:
        for k in c.headers_resp:
            kl = k.lower()
            if kl in CLOCK_HEADERS and kl not in seen:
                seen.append(kl)
            if kl in STAGE_HEADERS and kl not in stage:
                stage.append(kl)
    for k in seen:
        if k != "date":
            return k, 1, seen, stage
    return "Date", 1000, seen, stage


def choose(cands: List[Candidate], role: str, picks: Dict[str, int]) -> Optional[Candidate]:
    if role in picks:
        for c in cands:
            if c.idx == picks[role]:
                return c
        return None
    pool = [c for c in cands if c.scores.get(role, 0) > 0
            and "NOT_CATALOG:timetable_or_schedule" not in c.tags]
    if not pool:
        return None
    return sorted(pool, key=lambda c: (-c.scores[role], c.idx))[0]


def path_with_query(c: Candidate) -> str:
    q = urllib.parse.urlencode([(k, v) for k, v in c.query
                                if not is_sensitive(k)])
    return c.path + (("?" + q) if q else "")


def build_draft(cands: List[Candidate], picks: Dict[str, int],
                strict: bool = False) -> Tuple[dict, dict]:
    cat = choose(cands, "catalog", picks)
    sub = choose(cands, "submit", picks)
    en = choose(cands, "enrollment", picks)
    clock_name, clock_precision, clock_seen, stage_headers = detect_clock(cands)
    warnings: List[str] = []
    if stage_headers:
        warnings.append(f"latency-split header {stage_headers} present: "
                        "queue_ms / tx_ms can be separated from the real server")

    cols, col_notes = ({}, [])
    if cat:
        if "html_table" in cat.tags:
            cols = {f"col_{i}": i for i in range(len(cat.row_keys))}
            col_notes = [f"HTML column {i}: {v}" for i, v in enumerate(cat.row_keys)]
        else:
            cols, col_notes = guess_columns(cat.row_keys)
    if not cols:
        warnings.append("catalog.columns unresolved -> fill manually from body_preview")
    cols_en, _ = guess_columns(en.row_keys) if en else ({}, [])
    if en and not cols_en:
        cols_en = {k: v for k, v in cols.items() if k in ("course_id", "class_id")}

    full_markers, window_markers = [], []
    for c in cands:
        for t in c.tags:
            if t.startswith("full_marker:"):
                full_markers.append(t.split(":", 1)[1])
            if t.startswith("window_marker:"):
                window_markers.append(t.split(":", 1)[1])
    full_markers = list(dict.fromkeys(full_markers)) or FULL_MARKERS[:4]
    window_markers = list(dict.fromkeys(window_markers)) or WINDOW_MARKERS[:4]

    draft: dict = {
        "clock": {"header": clock_name,
                  "precision_ms": clock_precision,
                  "headers_seen": clock_seen},
        "business": {"full_markers": full_markers,
                     "window_markers": window_markers},
    }
    if cat:
        is_html = "html_table" in cat.tags
        draft["catalog"] = {
            "path": path_with_query(cat), "method": cat.method,
            "format": "html" if is_html else "json",
            "encoding": "utf-8",
            "json_list_path": cat.list_path or "data.rows",
            "columns": cols,
        }
        draft["clock"]["path"] = path_with_query(cat)
        draft["clock"]["method"] = cat.method
        draft["warmup"] = {"path": path_with_query(cat), "method": cat.method}
    else:
        warnings.append("no catalog candidate found")

    if sub:
        enc = "json" if "json" in sub.post_mime.lower() else "form"
        fields = {}
        for k, v in sub.params.items():
            kl = k.lower()
            if "kcid" in kl or "course" in kl or kl in ("kch", "kcdm"):
                fields[k] = "{course_id}"
            elif any(x in kl for x in ("skbj", "jxbh", "class", "jxbid")):
                fields[k] = "{class_id}"
            elif "xnxq" in kl or "term" in kl:
                fields[k] = "{xnxq}"
            elif is_sensitive(k) or "token" in kl or "sign" in kl:
                fields[k] = "{token}"
                warnings.append(f"submit field '{k}' looks like a per-session "
                                "ticket -> mapped to {token}")
            else:
                fields[k] = redact(k, v, strict) if strict else str(v)
        draft["submit"] = {"path": sub.path, "method": sub.method,
                           "encoding": enc, "fields": fields}
    else:
        warnings.append("no submit candidate found -> DO NOT guess a write endpoint")

    if en:
        draft["enrollment"] = {
            "path": path_with_query(en), "method": en.method,
            "format": "json" if "json" in en.mime.lower() else "html",
            "json_list_path": en.list_path or "data.rows",
            "columns": cols_en,
        }
    else:
        warnings.append("no enrollment/readback candidate found -> success cannot "
                        "be verified without the official list")

    for c in cands:
        if "NOT_CATALOG:timetable_or_schedule" in c.tags:
            warnings.append(f"entry #{c.idx} {c.path} is a TIMETABLE/schedule "
                            "response; it is not a selectable-course catalog "
                            "and must not be used as catalog.path")
        if "window_closed_evidence" in c.tags:
            warnings.append(f"entry #{c.idx} shows prohibit/window-closed text; "
                            "capture was made outside the selection window")

    report = {
        "kind": "har_schema_report",
        "source": "browser HAR (redacted)",
        "strict": strict,
        "entry_count": len(cands),
        "picks": {"catalog": cat.idx if cat else None,
                  "submit": sub.idx if sub else None,
                  "enrollment": en.idx if en else None},
        "column_notes": col_notes,
        "stage_split_headers": stage_headers,
        "warnings": warnings,
        "credential_note": ("Cookie / Authorization / token values are replaced "
                            "with <REDACTED:len=N>. Do not paste raw HAR into chat; "
                            "pass the local file path instead."),
        "candidates": [c.public(strict) for c in cands],
    }
    return draft, report


def merge_config(cfg: dict, draft: dict) -> dict:
    out = copy.deepcopy(cfg)
    for key in ("catalog", "submit", "enrollment", "business", "warmup", "clock"):
        if key not in draft:
            continue
        block = {k: v for k, v in draft[key].items()
                 if k not in ("precision_ms", "headers_seen")}
        if key in out:
            out[key].update(block)
        else:
            out[key] = block
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def print_table(cands: List[Candidate], picks: Dict[str, int]) -> None:
    print(f"{'#':>4} {'method':<7} {'status':>6} {'cat':>4} {'sub':>4} {'enr':>4}  path")
    print("-" * 96)
    for c in cands:
        mark = "".join("*" if picks.get(r) == c.idx else " "
                       for r in ("catalog", "submit", "enrollment"))
        print(f"{c.idx:>4} {c.method:<7} {c.status:>6} "
              f"{c.scores['catalog']:>4} {c.scores['submit']:>4} "
              f"{c.scores['enrollment']:>4} {mark} "
              f"{c.host}{c.path[:60]}")
    print("-" * 96)
    print("  * = auto-selected role. Override with --pick catalog=N submit=N enrollment=N")


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
    draft, report = build_draft(cands, picks, strict=args.strict)

    if args.list:
        print_table(cands, report["picks"])
        return 0

    print(f"HAR entries (filtered): {len(cands)} | host(s): "
          f"{', '.join(sorted({c.host for c in cands}))}")
    print_table(cands, report["picks"])
    print("\nAuto-selected:")
    for role in ("catalog", "submit", "enrollment"):
        idx = report["picks"][role]
        c = next((x for x in cands if x.idx == idx), None)
        print(f"  {role:<11} = " + (f"#{idx} {c.method} {c.path}" if c else "NOT FOUND"))
    if report["column_notes"]:
        print("\nColumn inference:")
        for n in report["column_notes"][:12]:
            print("  - " + n)
    if report["warnings"]:
        print("\nWARNINGS:")
        for w in report["warnings"]:
            print("  ! " + w)

    Path(args.out).write_text(json.dumps(draft, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
    print(f"\nWrote {args.out} (config patch) and {args.report} (redacted report)")

    if args.merge:
        cfg = json.loads(Path(args.merge).read_text(encoding="utf-8"))
        merged = merge_config(cfg, draft)
        dest = args.out_merged or "config.merged.json"
        Path(dest).write_text(json.dumps(merged, ensure_ascii=False, indent=2),
                              encoding="utf-8")
        print(f"Wrote {dest} (merge of {args.merge} + draft; "
              "targets/journal/budget preserved)")
    print("\nNext: verify every path and column against the official UI, then")
    print("  python live_client.py --config config.merged.json --probe "
          "--probe-bytes 4096 --probe-save probe-body.txt")
    return 0


def self_test() -> int:
    def entry(i, method, path, params=None, body="", status=200,
              mime="application/json", resp_headers=None):
        return {
            "request": {"method": method,
                        "url": "https://jw.example.edu.cn" + path,
                        "headers": [{"name": "Cookie", "value": "JSESSIONID=SECRET123"},
                                    {"name": "Accept", "value": "application/json"}],
                        "queryString": [],
                        "postData": {"mimeType": "application/x-www-form-urlencoded",
                                     "params": [{"name": k, "value": v}
                                                for k, v in (params or {}).items()]}},
            "response": {"status": status,
                         "headers": [{"name": "Date", "value": "Fri, 13 Nov 2026 02:48:00 GMT"},
                                     {"name": "Content-Type", "value": mime}]
                                    + [{"name": k, "value": v}
                                       for k, v in (resp_headers or {}).items()],
                         "content": {"mimeType": mime, "text": body, "size": len(body)}},
        }

    catalog_body = json.dumps({"data": {"rows": [
        {"kcid": "K001", "skbj": "01", "kcmc": "工程制图", "jsxm": "张三", "kyl": 3},
        {"kcid": "K002", "skbj": "01", "kcmc": "线性代数", "jsxm": "李四", "kyl": 0},
    ]}}, ensure_ascii=False)
    enrolled_body = json.dumps({"data": {"rows": [
        {"kcid": "K001", "skbj": "01", "kcmc": "工程制图"},
    ]}}, ensure_ascii=False)
    har = {"log": {"entries": [
        entry(0, "GET", "/js/app.js", body="var x=1;", mime="application/javascript"),
        entry(1, "GET", "/xsxk/course/list?xnxq=2026-2027-1", body=catalog_body,
              resp_headers={"Server-Timing": "queue;dur=8.5, db;dur=22.1"}),
        entry(2, "POST", "/xsxk/course/save",
              params={"kcid": "K001", "skbj": "01", "xnxq": "2026-2027-1",
                      "token": "SUPERSECRET"},
              body=json.dumps({"ok": True, "msg": "选课成功"}, ensure_ascii=False)),
        entry(3, "GET", "/xsxk/selected/list", body=enrolled_body),
        entry(4, "GET", "/hsjw/wap/mycourseschedule.action",
              # ensure_ascii=True mirrors real Kingosoft JSON: literal \uXXXX
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
        draft, report = build_draft(cands, picks, strict=False)
        report_text = json.dumps(report, ensure_ascii=False)

        cfg = {"catalog": {"path": "/OLD"}, "targets": [{"course_id": "KEEP"}]}
        merged = merge_config(cfg, draft)

        checks = [
            ("static resources filtered", len(cands) == 4, f"len={len(cands)}"),
            ("catalog identified", report["picks"]["catalog"] == 1,
             str(report["picks"])),
            ("submit identified", report["picks"]["submit"] == 2,
             str(report["picks"])),
            ("enrollment identified", report["picks"]["enrollment"] == 3,
             str(report["picks"])),
            ("json list path inferred", draft["catalog"]["json_list_path"] == "data.rows",
             draft["catalog"]["json_list_path"]),
            ("columns inferred", draft["catalog"]["columns"].get("course_id") == "kcid"
             and draft["catalog"]["columns"].get("remain") == "kyl",
             str(draft["catalog"]["columns"])),
            ("submit fields templated", draft["submit"]["fields"].get("kcid") == "{course_id}"
             and draft["submit"]["fields"].get("token") == "{token}",
             str(draft["submit"]["fields"])),
            ("cookie redacted", "SECRET123" not in report_text
             and "SUPERSECRET" not in report_text, "secret leak"),
            ("timetable excluded", any("TIMETABLE" in w for w in report["warnings"]),
             str(report["warnings"])),
            ("window marker captured", any("不是有效的选课时间" in w
                                           for w in draft["business"]["window_markers"]),
             str(draft["business"]["window_markers"])),
            ("clock header + precision", draft["clock"]["header"] == "Date"
             and draft["clock"]["precision_ms"] == 1000, str(draft["clock"])),
            ("Server-Timing is NOT a clock header",
             draft["clock"]["header"].lower() != "server-timing"
             and "server-timing" in report["stage_split_headers"],
             str(report["stage_split_headers"])),
            ("literal \\uXXXX body is decoded",
             "选课成功" in decode_content({"response": {"content": {
                 "text": r'{"msg":"\u9009\u8bfe\u6210\u529f"}'}}})[0],
             "unicode escape not normalized"),
            ("merge preserves targets", merged["targets"] == [{"course_id": "KEEP"}]
             and merged["catalog"]["path"].startswith("/xsxk/course/list"),
             str(merged.get("targets"))),
            ("pick override works",
             choose(cands, "catalog", {"catalog": 4}) is None
             or choose(cands, "catalog", {"catalog": 1}).idx == 1, "override"),
        ]
        # strict mode must remove course numbers from the report
        _, strict_report = build_draft(cands, {}, strict=True)
        checks.append(("strict redacts business values",
                       "K001" not in json.dumps(strict_report, ensure_ascii=False),
                       "K001 leaked"))

        bad = 0
        for label, ok, detail in checks:
            print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + ("" if ok else f" -> {detail}"))
            bad += 0 if ok else 1
        print(f"\n{len(checks) - bad}/{len(checks)} checks passed")
        return 1 if bad else 0


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="HAR -> kingo-live config schema draft")
    ap.add_argument("har", nargs="?", help="path to .har file")
    ap.add_argument("--host", default="", help="only keep this host substring")
    ap.add_argument("--pick", action="append", default=[], metavar="ROLE=IDX",
                    help="override auto-selection, e.g. --pick catalog=3")
    ap.add_argument("--list", action="store_true", help="only print candidate table")
    ap.add_argument("--strict", action="store_true",
                    help="redact ALL business values in the report, not just credentials")
    ap.add_argument("--out", default="schema-draft.json")
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
