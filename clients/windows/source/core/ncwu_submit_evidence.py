"""Value-free offline inventory of an S202 first-party submission HAR.

Only exact-method, exact-host, exact-path matching is allowed. This never
replays requests, interprets an HTTP 200 as enrollment success, or exports
cookies, student IDs, teaching-class values or submission contents.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from urllib.parse import urlsplit

from core.ncwu_contract_capture import (
    HOST, _MAX_ENTRIES, _MAX_FIELDS, _MAX_FILE, _body_keys,
    _mime_family, _query_keys, _response_shape,
)
from core.ncwu_live_elective import SELECTED_PAGE
from core.ncwu_submission_transport import SUBMIT_PATH


@dataclass(frozen=True)
class S202SubmitEvidence:
    code: str
    submit_count: int = 0
    results_page_count: int = 0
    request_fields: tuple[str, ...] = ()
    query_fields: tuple[str, ...] = ()
    request_formats: tuple[str, ...] = ()
    response_shapes: tuple[str, ...] = ()
    response_fields: tuple[str, ...] = ()
    result_fields: tuple[str, ...] = ()
    http_codes: tuple[int, ...] = ()

    @property
    def message(self) -> str:
        if self.code != "parsed":
            return "HAR 文件结构不合法或大小超限，未得到任何接口证据。"
        expected = {"xh", "xn", "xq", "lcid", "xktype", "hidOption", "items"}
        missing = sorted(expected - set(self.request_fields))
        return (
            f"正式 S202 提交 POST 样本：{self.submit_count} 条\n"
            f"官方已选结果页 GET 样本：{self.results_page_count} 条\n"
            f"HTTP 状态码：{', '.join(map(str, self.http_codes)) or '未知'}\n"
            f"URL 参数名：{', '.join(self.query_fields) or '无'}\n"
            f"提交字段名：{', '.join(self.request_fields) or '未获取'}\n"
            f"请求格式：{', '.join(self.request_formats) or '未知'}\n"
            f"响应结构：{', '.join(self.response_shapes) or '未知'}\n"
            f"响应外层字段名：{', '.join(self.response_fields) or '未获取'}\n"
            f"响应 result 字段名：{', '.join(self.result_fields) or '未获取'}\n"
            f"当前传输层预期字段未见：{', '.join(missing) or '无'}\n\n"
            "本报告仅含字段名称、状态码和结构类型，无任何字段取值。"
            "样本存在不证明课程与内部教学班映射正确，不证明写入获得许可，"
            "HTTP 200 不表示选课成功；结果页 GET 也不代表找到本人已选记录。"
            "没有发送任何学校请求。"
        )


def inspect_s202_submit_har(raw: bytes) -> S202SubmitEvidence:
    """Analyze a user-owned local HAR; return no dynamic values."""
    if not isinstance(raw, bytes) or not raw or len(raw) > _MAX_FILE:
        return S202SubmitEvidence("invalid")
    try:
        document = json.loads(raw.decode("utf-8-sig"))
        log = document["log"]
        entries = log["entries"]
        if not isinstance(log, dict) or not isinstance(entries, list):
            raise ValueError("invalid HAR log")
        if len(entries) > _MAX_ENTRIES:
            raise ValueError("HAR too large")
    except (TypeError, KeyError, ValueError, UnicodeError, RecursionError):
        return S202SubmitEvidence("invalid")

    submit_count = 0
    results_page_count = 0
    req_names: set[str] = set()
    query_names: set[str] = set()
    formats: set[str] = set()
    shapes: set[str] = set()
    outer_names: set[str] = set()
    result_names: set[str] = set()
    codes: set[int] = set()
    for item in entries:
        if not isinstance(item, dict):
            continue
        req, resp = item.get("request"), item.get("response")
        if not isinstance(req, dict):
            continue
        try:
            location = urlsplit(req.get("url", ""))
        except (ValueError, TypeError):
            continue
        if location.scheme != "https" or location.netloc != HOST:
            continue
        if location.path == SELECTED_PAGE and req.get("method") == "GET":
            results_page_count = min(100, results_page_count + 1)
            continue
        if location.path != SUBMIT_PATH or req.get("method") != "POST":
            continue
        if submit_count >= 100:
            continue
        submit_count += 1
        query_names.update(_query_keys(req, location.query))
        body = req.get("postData")
        req_names.update(_body_keys(body))
        if isinstance(body, dict):
            formats.add(_mime_family(body.get("mimeType")))
        if isinstance(resp, dict):
            status = resp.get("status")
            if type(status) is int and 100 <= status <= 599:
                codes.add(status)
            outer, nested, shape, _ = _response_shape(resp.get("content"))
            shapes.add(shape)
            outer_names.update(outer)
            result_names.update(nested)
    return S202SubmitEvidence(
        "parsed", submit_count, results_page_count,
        tuple(sorted(req_names))[:_MAX_FIELDS],
        tuple(sorted(query_names))[:_MAX_FIELDS],
        tuple(sorted(formats)), tuple(sorted(shapes)),
        tuple(sorted(outer_names))[:_MAX_FIELDS],
        tuple(sorted(result_names))[:_MAX_FIELDS],
        tuple(sorted(codes)),
    )
