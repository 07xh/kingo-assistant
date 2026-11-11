"""Offline, privacy-preserving HAR structure audit for authorized NCWU/Xiqueer captures.

No request replay, decryption, credential extraction, or API verification.
The summary is deliberately restricted to counts and fixed status labels.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path
from urllib.parse import urlsplit

MAX_HAR_BYTES = 25 * 1024 * 1024
MAX_ENTRIES = 3000
MAX_RESPONSE_TEXT = 1024 * 1024
_TIMETABLE_HOST = 'jwmis.ncwu.edu.cn'
_TIMETABLE_PATH = '/hsjw/wap/mycourseschedule.action'
_GATEWAY_HOST = 'api.xiqueer.com'
_GATEWAY_PATH = '/manager/wap/wapController.jsp'
_CLOSED_WINDOW_MESSAGE = '当前时间不是有效的选课时间区段'


def _canonical_path(value: str) -> str:
    """Normalize redundant separators only; never fetch the path."""
    return '/' + '/'.join(segment for segment in value.split('/') if segment)


def _is_timetable_schema(response: dict) -> bool:
    """Validate the *shape* of the known NCWU timetable response, never values."""
    if response.get('status') != 200:
        return False
    content = response.get('content')
    if not isinstance(content, dict) or content.get('encoding') not in (None, ''):
        return False
    raw = content.get('text')
    if not isinstance(raw, str) or len(raw) > MAX_RESPONSE_TEXT:
        return False
    try:
        obj = json.loads(raw)
    except (ValueError, TypeError):
        return False
    if not isinstance(obj, dict):
        return False
    if not all(isinstance(obj.get(key), str) for key in ('xn', 'xq', 'zc')):
        return False
    return (all(isinstance(obj.get(f'week{n}'), list) for n in range(1, 8))
            and isinstance(obj.get('sjhjinfo'), list))


def _closed_window_observed(response: dict) -> bool:
    """Match an exact known restriction sentence without exposing response text."""
    content = response.get('content')
    if not isinstance(content, dict) or content.get('encoding') not in (None, ''):
        return False
    raw = content.get('text')
    return (isinstance(raw, str) and len(raw) <= MAX_RESPONSE_TEXT
            and _CLOSED_WINDOW_MESSAGE in raw)


@dataclass(frozen=True)
class HarSummary:
    entries_total: int
    matching_school_origin: int
    method_counts: dict[str, int]
    status_counts: dict[str, int]
    response_type_counts: dict[str, int]
    xiqueer_gateway_requests: int = 0
    ncwu_timetable_schema_matches: int = 0
    selection_window_closed_observations: int = 0


def summarize_har(path: Path, *, official_domain: str = 'ncwu.edu.cn') -> HarSummary:
    """Count safe structural evidence; do not expose unredacted capture content."""
    path = Path(path)
    if path.is_symlink() or not path.is_file():
        raise ValueError('HAR 文件不存在或是符号链接')
    if path.stat().st_size > MAX_HAR_BYTES:
        raise ValueError('HAR 文件超过离线检查容量上限')
    try:
        raw = json.loads(path.read_text(encoding='utf-8-sig'))
    except (ValueError, UnicodeError, OSError) as error:
        raise ValueError('HAR 不是有效 UTF-8 JSON') from error
    entries = raw.get('log', {}).get('entries') if isinstance(raw, dict) else None
    if not isinstance(entries, list) or len(entries) > MAX_ENTRIES:
        raise ValueError('HAR 缺少 entries，或请求条目过多')
    methods: Counter[str] = Counter()
    statuses: Counter[str] = Counter()
    types: Counter[str] = Counter()
    matched = 0
    gateway_count = 0
    timetable_hits = 0
    closed_hits = 0
    official_domain = official_domain.casefold().strip('.')
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        request, response = entry.get('request'), entry.get('response')
        if not isinstance(request, dict) or not isinstance(response, dict):
            continue
        try:
            target = urlsplit(request.get('url', ''))
            host = (target.hostname or '').casefold().strip('.')
        except (ValueError, TypeError, AttributeError):
            continue
        if target.scheme != 'https':
            continue
        path_only = _canonical_path(target.path)
        if host == _GATEWAY_HOST and path_only == _GATEWAY_PATH:
            gateway_count += 1
            if _closed_window_observed(response):
                closed_hits += 1
        if not (host == official_domain or host.endswith('.' + official_domain)):
            continue
        matched += 1
        method = str(request.get('method', 'UNKNOWN')).upper()
        methods[method if method in ('GET', 'POST', 'HEAD', 'OPTIONS') else 'OTHER'] += 1
        status = response.get('status')
        statuses[str(status) if type(status) is int and 100 <= status <= 599 else 'UNKNOWN'] += 1
        content = response.get('content')
        mime = content.get('mimeType', '') if isinstance(content, dict) else ''
        mime = str(mime).split(';', 1)[0].lower().strip()
        types[mime if mime in ('text/html', 'application/json', 'text/plain',
                               'application/xml', 'text/xml') else 'other/unknown'] += 1
        if host == _TIMETABLE_HOST and path_only == _TIMETABLE_PATH:
            if _is_timetable_schema(response):
                timetable_hits += 1
    return HarSummary(len(entries), matched, dict(sorted(methods.items())),
                      dict(sorted(statuses.items())), dict(sorted(types.items())),
                      gateway_count, timetable_hits, closed_hits)


def format_summary(summary: HarSummary) -> str:
    """Only render allowlisted counts and fixed text (no paths/personal data)."""
    def fields(mapping: dict[str, int]) -> str:
        return '、'.join(f'{name}: {count}' for name, count in mapping.items()) or '无'
    return (f'HAR 总请求：{summary.entries_total} 条\n'
            f'华水官方 HTTPS 域名请求：{summary.matching_school_origin} 条\n'
            f'方法统计：{fields(summary.method_counts)}\n'
            f'HTTP 状态：{fields(summary.status_counts)}\n'
            f'响应类型：{fields(summary.response_type_counts)}\n\n'
            f'喜鹊儿统一业务入口请求：{summary.xiqueer_gateway_requests} 条\n'
            f'华水个人课表 JSON 结构匹配：{summary.ncwu_timetable_schema_matches} 条\n'
            f'选课时段关闭提示：{summary.selection_window_closed_observations} 条\n\n'
            '真实可选课程目录：未核验；剩余名额字段：未核验。\n'
            '只分析本地已获得的 HAR，不重放请求、解析签名或展示原文。\n'
            'POST 也可能触发写入；课表或选课记录不等于可选课程目录。')
