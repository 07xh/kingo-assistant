"""Candidate-only parsing of historical first-party NCWU selected-course HTML.

A prior authorized local HAR showed the user's selected-course display table
includes paired <td name="kcdm"> and <td name="skbjdm"> fields.
This module reads those fields from one *current* HTML document and provides
in-memory, unverified candidates. It never makes university HTTP requests and
is NOT an EnrollmentEngine official readback source.

The presence of the field names does not verify current student, term, round,
registration status or signed contract. It cannot create an attestation.
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from bs4 import BeautifulSoup

_MAX_HTML = 512 * 1024
_MAX_CANDIDATES = 200
_HEADERS = frozenset({"课程", "选课状态", "上课班级代码"})


@dataclass(frozen=True, slots=True)
class HistoricalClassCandidate:
    course_code: str
    internal_class_id: str
    displayed_status: str = ""
    status_code: str = ""

    @property
    def observed_selected_pair(self) -> bool:
        # Only the {display=选中, code=03} pair appeared in the 2026-11-03
        # capture. It is not a validated success or selection-round status.
        return self.displayed_status == "选中" and self.status_code == "03"


@dataclass(frozen=True, slots=True)
class HistoricalClassInspection:
    recognized_tables: int = 0
    candidate_rows: tuple[HistoricalClassCandidate, ...] = ()
    ignored_rows: int = 0

    @property
    def message(self) -> str:
        return (
            f"检测到 {self.recognized_tables} 张包含官方课程/状态表头的表；"
            f"识别 {len(self.candidate_rows)} 条含 kcdm/skbjdm 的候选行，"
            f"另有 {self.ignored_rows} 行缺少唯一身份字段或值。"
            "此结果只是一份来源字段候选，尚未关联本人账号、学期、轮次"
            "与完整接口验收，不能用于自动提交或确认选课成功。"
        )


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _safe_token(text: str) -> bool:
    return (
        isinstance(text, str) and text == text.strip()
        and 1 <= len(text) <= 100
        and all(ord(ch) >= 32 and ch not in "\x7f\r\n" for ch in text)
    )


def inspect_known_result_candidates(html: str) -> HistoricalClassInspection:
    """Parse a bounded result table with exact, explicit internal ID cell names.

    Do not infer identifiers from display values, column position alone, or
    class-name text. Every accepted row has one kcdm and one skbjdm cell.
    """
    if not isinstance(html, str) or not html or len(html) > _MAX_HTML:
        return HistoricalClassInspection()
    soup = BeautifulSoup(html, "html.parser")
    if soup.find("input", attrs={"type": "password"}) is not None:
        return HistoricalClassInspection()

    recognized = 0
    candidates: list[HistoricalClassCandidate] = []
    ignored = 0
    seen: set[tuple[str, str]] = set()
    for table in soup.find_all("table")[:30]:
        # The response embeds distinct laboratory and class tables. Do not
        # accidentally join a table's nested rows with a different header.
        rows = [tr for tr in table.find_all("tr")
                if tr.find_parent("table") is table]
        if not rows:
            continue
        headings = rows[0].find_all(("th", "td"), recursive=False)
        labels = [_norm(col.get_text(" ", strip=True)) for col in headings]
        if not _HEADERS.issubset(labels):
            continue
        recognized += 1
        status_column = labels.index("选课状态")
        for row in rows[1:]:
            if len(candidates) >= _MAX_CANDIDATES:
                break
            cells = row.find_all("td", recursive=False)
            named: dict[str, list] = {
                "kcdm": [], "skbjdm": [], "xk_status_m": [],
            }
            for cell in cells:
                key = str(cell.get("name", "")).lower().strip()
                if key in named:
                    named[key].append(cell)
            if (len(named["kcdm"]) != 1 or len(named["skbjdm"]) != 1
                    or len(named["xk_status_m"]) > 1):
                ignored += 1
                continue
            code = named["kcdm"][0].get_text(" ", strip=True)
            internal = named["skbjdm"][0].get_text(" ", strip=True)
            if not (_safe_token(code) and _safe_token(internal)):
                ignored += 1
                continue
            identity = code, internal
            if identity in seen:
                continue
            seen.add(identity)
            status = ""
            if status_column < len(cells):
                status = cells[status_column].get_text(" ", strip=True)[:100]
            status_code = ""
            if named["xk_status_m"]:
                raw_code = named["xk_status_m"][0].get_text(" ", strip=True)
                if re.fullmatch(r"[A-Za-z0-9_-]{1,16}", raw_code):
                    status_code = raw_code
            candidates.append(
                HistoricalClassCandidate(code, internal, status, status_code))
    return HistoricalClassInspection(recognized, tuple(candidates), ignored)
