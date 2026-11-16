"""Offline-only evidence inspection of the NCWU S202 elective DataTable header.

This does NOT parse availability, generate a submission, or make HTTP calls.
The 2026-11-16 capture established a 17-column header while the selection
window was closed, so course rows, remaining seats, and bid rules are unknown.
"""
from __future__ import annotations

from dataclasses import dataclass

from bs4 import BeautifulSoup

from core.client import html_encoding

EXPECTED_HEADINGS = (
    "head_kc", "head_xf", "head_zxs", "head_lb", "head_kcsx",
    "head_skbh", "head_skbjmc", "head_xkfs", "head_rkjs",
    "head_xk_points", "head_is_buy_book", "head_is_cx",
    "head_xk_status", "head_operation", "head_kcdm",
    "head_kclb1", "head_kclb2",
)
_REQUIRED = frozenset(("head_kc", "head_skbh", "head_xk_status", "head_kcdm"))
_EXPECTED = frozenset(EXPECTED_HEADINGS)
_MAX_HTML_BYTES = 2 * 1024 * 1024


@dataclass(frozen=True)
class ElectiveTableEvidence:
    code: str
    recognized_headers: int = 0
    candidate_data_rows: int = 0

    @property
    def message(self) -> str:
        if self.code == "header_observed":
            return (
                f"本机 HTML 包含 {self.recognized_headers} 个已记录的选课表头，"
                f"找到 {self.candidate_data_rows} 行疑似数据。"
                "尚未确认课程记录、真实余量、选课轮次或任何提交协议。"
            )
        return {
            "expired": "内容含凭证失效提示；不能认定为选课表",
            "header_incomplete": (
                f"找到 {self.recognized_headers} 个已知字段，但不满足完整表头结构；"
                "请核对原始页面，未标记为真实目录"
            ),
            "ambiguous_table": "页面有多个匹配的选课表，无法唯一判定",
            "schema_unverified": "未找到与本次抓包匹配的选课表头；没有伪造课程或余量",
            "invalid_response": "HTML 无效、过大或编码声明冲突；未解析内容",
        }[self.code]


def inspect_elective_table(raw: bytes, content_type: str = "") -> ElectiveTableEvidence:
    """Inspect local HTML structure only; no course/student values are exposed.

    This intentionally does not expose a `remain` field: none of the 17
    captured headers establishes remaining-seat semantics. A candidate row is
    neither an available offering nor an authorization to submit.
    """
    if (not isinstance(raw, bytes) or not raw
            or len(raw) > _MAX_HTML_BYTES):
        return ElectiveTableEvidence("invalid_response")
    try:
        text = raw.decode(html_encoding(raw, content_type), errors="strict")
    except (UnicodeError, ValueError, LookupError):
        return ElectiveTableEvidence("invalid_response")
    except Exception:
        return ElectiveTableEvidence("invalid_response")
    if "凭证已失效" in text or "登录已失效" in text or "请先登录" in text:
        return ElectiveTableEvidence("expired")
    soup = BeautifulSoup(text, "html.parser")
    candidates = []
    incomplete = 0
    for table in soup.find_all("table"):
        found = {
            tag.get("id") for tag in table.find_all(attrs={"id": True})
            if tag.get("id") in _EXPECTED
        }
        incomplete = max(incomplete, len(found))
        if _REQUIRED.issubset(found):
            candidates.append((table, found))
    if len(candidates) > 1:
        return ElectiveTableEvidence("ambiguous_table")
    if not candidates:
        return (ElectiveTableEvidence("header_incomplete", incomplete)
                if incomplete else ElectiveTableEvidence("schema_unverified"))
    table, found = candidates[0]
    if found != _EXPECTED:
        return ElectiveTableEvidence("header_incomplete", len(found))
    rows = sum(
        1 for tr in table.find_all("tr")
        if tr.find("td") is not None
        and not tr.find(attrs={"id": lambda value: isinstance(value, str)
                              and value.startswith("head_")})
    )
    return ElectiveTableEvidence("header_observed", len(found), rows)
