"""Offline static HTML/JS hints for the *already observed* NCWU S202 action.

The caller supplies locally saved first-party source files. This module has no
network, cookie, replay, or enrollment capabilities. A path mention or matching
HTML form is evidence of source structure only, never a verified write contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Sequence
from urllib.parse import urlsplit

from core.ncwu_contract_capture import HOST, _FIELD, _MAX_FIELDS
from core.ncwu_submission_transport import SUBMIT_PATH

MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
MAX_SOURCES = 12
_ALLOWED_SUFFIXES = frozenset((".html", ".htm", ".js"))
_REQUIRED_FIELDS = frozenset(("xh", "xn", "xq", "lcid", "xktype", "hidOption", "items"))


def _is_target_action(value: str) -> bool:
    """Accept an exact same-origin HTTPS action or an exact absolute path."""
    if not isinstance(value, str) or len(value) > 2048:
        return False
    try:
        target = urlsplit(value.strip())
    except ValueError:
        return False
    if target.scheme or target.netloc:
        return (
            target.scheme == "https"
            and target.netloc == HOST
            and target.path == SUBMIT_PATH
        )
    return target.path == SUBMIT_PATH


class _SubmitFormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.in_target_form = False
        self.target_form_count = 0
        self.fields: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag.lower() == "form":
            self.in_target_form = (
                values.get("method", "get").strip().lower() == "post"
                and _is_target_action(values.get("action", ""))
            )
            if self.in_target_form:
                self.target_form_count += 1
        elif self.in_target_form and tag.lower() in ("input", "select", "textarea", "button"):
            name = values.get("name")
            if isinstance(name, str) and _FIELD.fullmatch(name):
                self.fields.add(name)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "form":
            self.in_target_form = False


@dataclass(frozen=True)
class StaticSubmitEvidence:
    code: str
    files_examined: int = 0
    target_form_count: int = 0
    path_reference_count: int = 0
    form_field_names: tuple[str, ...] = ()

    @property
    def evidence_state(self) -> str:
        if self.code != "parsed":
            return "invalid"
        if self.target_form_count and _REQUIRED_FIELDS.issubset(self.form_field_names):
            return "form_fields_observed"
        if self.target_form_count:
            return "partial_form_observed"
        if self.path_reference_count:
            return "text_reference_only"
        return "no_static_reference"

    @property
    def message(self) -> str:
        if self.code != "parsed":
            return "静态资源格式、数量或大小不符合要求；没有生成接口证据。"
        states = {
            "form_fields_observed": "匹配到提交表单及预期字段名称；字段取值来源未验证",
            "partial_form_observed": "匹配到提交表单，但预期字段名称不完整",
            "text_reference_only": "只发现已知路径的文本引用，不能确认接口调用",
            "no_static_reference": "没有观察到已知提交路径的静态引用",
        }
        missing = sorted(_REQUIRED_FIELDS - set(self.form_field_names))
        return (
            f"已本地检查静态文件：{self.files_examined} 个\n"
            f"精确匹配的 POST form：{self.target_form_count} 个\n"
            f"已知提交路径文本引用：{self.path_reference_count} 个文件\n"
            f"匹配 form 字段名：{', '.join(self.form_field_names) or '未发现'}\n"
            f"预期字段名未见：{', '.join(missing) or '无'}\n"
            f"证据状态：{states[self.evidence_state]}\n\n"
            "文本匹配可能来自注释或未执行代码。静态 HTML/JS 不能证明当前"
            "选课轮次、动态参数、课程与教学班绑定、学校许可或选课成功。"
            "不导出字段取值、源代码、文件路径或凭证；没有发送任何网络请求，"
            "也没有创建可提交的请求模板。"
        )


def inspect_static_submit_sources(sources: Sequence[tuple[str, bytes]]) -> StaticSubmitEvidence:
    """Inspect saved source bytes only; source labels must be file suffixes.

    Any rejected file invalidates the batch, avoiding partial/stale evidence.
    Do not pass file names or paths as labels: reports never include them.
    """
    if not isinstance(sources, (list, tuple)) or not 1 <= len(sources) <= MAX_SOURCES:
        return StaticSubmitEvidence("invalid")
    total_bytes = 0
    form_count = 0
    text_count = 0
    fields: set[str] = set()
    for suffix, raw in sources:
        if not isinstance(suffix, str) or suffix.lower() not in _ALLOWED_SUFFIXES:
            return StaticSubmitEvidence("invalid")
        if not isinstance(raw, bytes) or not raw or len(raw) > MAX_SOURCE_BYTES:
            return StaticSubmitEvidence("invalid")
        total_bytes += len(raw)
        if total_bytes > MAX_TOTAL_BYTES:
            return StaticSubmitEvidence("invalid")
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            try:
                text = raw.decode("gb18030")
            except UnicodeDecodeError:
                return StaticSubmitEvidence("invalid")
        if SUBMIT_PATH in text:
            text_count += 1
        if suffix.lower() in (".html", ".htm"):
            parser = _SubmitFormParser()
            try:
                parser.feed(text)
                parser.close()
            except (ValueError, AssertionError):
                return StaticSubmitEvidence("invalid")
            form_count += parser.target_form_count
            fields.update(parser.fields)
    return StaticSubmitEvidence(
        code="parsed",
        files_examined=len(sources),
        target_form_count=form_count,
        path_reference_count=text_count,
        form_field_names=tuple(sorted(fields))[:_MAX_FIELDS],
    )
