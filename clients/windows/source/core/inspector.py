"""Offline-only HTML table structure inspector.

Never prints row content, names, URLs, hidden inputs or session identifiers.
The suggested indexes are UNVERIFIED hints, not an authoritative NCWU mapping.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

MAX_HTML_BYTES = 4_000_000
MAX_TABLES = 60
MAX_ROWS = 1000

_HEADER_ALIASES = {
    "course_id": ("课程号", "课程代码", "课程编号", "课号", "kcdm", "kch"),
    "course_name": ("课程名称", "课程名", "课程", "kcmc"),
    "class_id": ("教学班号", "教学班代码", "教学班编号", "教学班", "上课班号", "skbh"),
    "teacher": ("任课教师", "授课教师", "主讲教师", "教师", "jsxm"),
    "remain": ("剩余名额", "剩余人数", "剩余容量", "剩余", "可选人数", "余量"),
    "capacity": ("班级容量", "选课容量", "容量", "限选人数"),
    "campus": ("上课校区", "校区"),
    "meeting": ("上课时间", "星期节次", "时间地点", "上课时间地点"),
}
_ALIASES = {alias.casefold(): key for key, aliases in _HEADER_ALIASES.items() for alias in aliases}
_WHITESPACE = re.compile(r"[\s:：*＊]+")


@dataclass(frozen=True)
class TableHint:
    table_number: int
    row_count: int
    field_indexes: dict[str, int]
    required_fields_found: bool
    ambiguous_headers: bool


def _decode(raw: bytes) -> str:
    if not raw or len(raw) > MAX_HTML_BYTES:
        raise ValueError("本机 HTML 为空或超过 4 MB，未读取")
    for codec in ("utf-8-sig", "gb18030"):
        try:
            return raw.decode(codec)
        except UnicodeDecodeError:
            continue
    raise ValueError("网页编码未知，拒绝丢弃不可识别字符")


def inspect_html(raw: bytes) -> list[TableHint]:
    """Return whitelisted structural metadata from a previously redacted local file."""
    soup = BeautifulSoup(_decode(raw), "lxml")
    hints: list[TableHint] = []
    for number, table in enumerate(soup.find_all("table", limit=MAX_TABLES), 1):
        rows = table.find_all("tr", limit=MAX_ROWS + 1)
        field_indexes: dict[str, int] = {}
        ambiguous = False
        for row in rows[:3]:
            # Old systems sometimes use TD cells for headings. We only accept
            # an all-TD row as a *header candidate* when at least three exact
            # whitelisted column labels match. Personal cell text is ignored.
            cells = row.find_all(["th", "td"], recursive=False)
            if not cells:
                continue
            known_header = row.find("th", recursive=False) is not None
            if not known_header:
                aliases = [
                    _ALIASES.get(_WHITESPACE.sub("", c.get_text(" ", strip=True)).casefold())
                    for c in cells[:51]
                ]
                if len(set(x for x in aliases if x is not None)) < 3:
                    continue
            candidate: dict[str, int] = {}
            for col, cell in enumerate(cells[:51]):
                label = _WHITESPACE.sub("", cell.get_text(" ", strip=True)).casefold()
                key = _ALIASES.get(label)
                if key:
                    if key in candidate:
                        ambiguous = True
                    else:
                        candidate[key] = col
            if len(candidate) > len(field_indexes):
                field_indexes = candidate
        if field_indexes:
            hints.append(TableHint(
                table_number=number,
                row_count=max(0, min(MAX_ROWS, len(rows) - 1)),
                field_indexes=field_indexes,
                required_fields_found=all(key in field_indexes for key in
                    ("course_id", "course_name", "teacher", "remain")),
                ambiguous_headers=ambiguous,
            ))
    return hints
