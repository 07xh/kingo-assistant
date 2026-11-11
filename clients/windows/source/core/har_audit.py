"""Strictly local, redacted HAR structure summary for authorized school traffic.

Does not replay requests, inspect credentials, read response bodies or infer
that a POST endpoint is read-only. Its output deliberately omits request URLs,
headers, query parameters, bodies, cookies, user identifiers and class names.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path
from urllib.parse import urlsplit

MAX_HAR_BYTES = 25 * 1024 * 1024
MAX_ENTRIES = 3000


@dataclass(frozen=True)
class HarSummary:
    entries_total: int
    matching_school_origin: int
    method_counts: dict[str, int]
    status_counts: dict[str, int]
    response_type_counts: dict[str, int]


def summarize_har(path: Path, *, official_domain: str = "ncwu.edu.cn") -> HarSummary:
    """Count only authorized organization hosts, never send or print HAR contents."""
    if not path.is_file():
        raise ValueError("HAR 文件不存在")
    if path.stat().st_size > MAX_HAR_BYTES:
        raise ValueError("HAR 文件超过离线检查容量上限")
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (ValueError, UnicodeError) as error:
        raise ValueError("HAR 不是有效 UTF-8 JSON") from error
    entries = raw.get("log", {}).get("entries") if isinstance(raw, dict) else None
    if not isinstance(entries, list) or len(entries) > MAX_ENTRIES:
        raise ValueError("HAR 缺少 entries，或请求条目过多")
    methods: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    types: Counter[str] = Counter()
    matched = 0
    official_domain = official_domain.casefold().strip(".")
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        request = entry.get("request")
        response = entry.get("response")
        if not isinstance(request, dict) or not isinstance(response, dict):
            continue
        try:
            target = urlsplit(request.get("url", ""))
        except (ValueError, TypeError):
            continue
        host = (target.hostname or "").casefold().strip(".")
        if target.scheme != "https" or not (host == official_domain or host.endswith("." + official_domain)):
            continue
        matched += 1
        method = str(request.get("method", "UNKNOWN")).upper()
        methods[method if method in ("GET", "POST", "HEAD", "OPTIONS") else "OTHER"] += 1
        status = response.get("status")
        statuses[str(status) if type(status) is int and 100 <= status <= 599 else "UNKNOWN"] += 1
        mime = response.get("content", {}).get("mimeType", "") if isinstance(response.get("content"), dict) else ""
        mime = str(mime).split(";", 1)[0].lower().strip()
        types[mime if mime in ("text/html", "application/json", "text/plain", "application/xml", "text/xml") else "other/unknown"] += 1
    return HarSummary(len(entries), matched, dict(sorted(methods.items())),
                      dict(sorted(statuses.items())), dict(sorted(types.items())))


def format_summary(summary: HarSummary) -> str:
    def fields(mapping: dict[str, int]) -> str:
        return "、".join(f"{name}: {count}" for name, count in mapping.items()) or "无"
    return (f"HAR 总请求：{summary.entries_total} 条\n"
            f"华水官方 HTTPS 域名请求：{summary.matching_school_origin} 条\n"
            f"方法统计：{fields(summary.method_counts)}\n"
            f"HTTP 状态：{fields(summary.status_counts)}\n"
            f"响应类型：{fields(summary.response_type_counts)}\n\n"
            "仅统计数量；只使用 URL 域名进行过滤，不展示或导出账号、Cookie、具体 URL、请求体及响应正文。\n"
            "POST 可能是写入操作；此报告不能证明接口只读、合法或真实可用。")
