"""Offline, value-free NCWU S202 interface contract evidence inventory.

Input is a local HAR from the owner's *normal, authorized* use of the school
website. Output contains only bounded field names, enumerated content shapes
and HTTP status codes; never request/response values, credentials, headers,
student/class identifiers, URLs with parameter values or raw payloads.

This does NOT validate a business contract or enable live calls to the
unverified initializer / teaching-class eligibility routes.
"""
from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlsplit

HOST = "jwmis.ncwu.edu.cn"
INIT_PATH = "/hsjw/STU_DynamicInitDataAction.do"
ELIGIBILITY_PATH = "/hsjw/jw/common/isSelectableSkbjdm.action"
KNOWN = (INIT_PATH, ELIGIBILITY_PATH)
_MAX_FILE = 16 * 1024 * 1024
_MAX_ENTRIES = 8000
_MAX_TEXT = 262144
_MAX_FIELDS = 100
_FIELD = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")


def _safe_keys(obj: Any) -> tuple[str, ...]:
    if not isinstance(obj, dict):
        return ()
    return tuple(sorted(
        k for k in obj if isinstance(k, str) and _FIELD.fullmatch(k)
    ))[:_MAX_FIELDS]


def _query_keys(req: dict, query: str) -> tuple[str, ...]:
    names: set[str] = set()
    entries = req.get("queryString")
    if isinstance(entries, list):
        for item in entries[:160]:
            if isinstance(item, dict) and isinstance(item.get("name"), str):
                names.add(item["name"])
    try:
        names.update(k for k, _ in parse_qsl(
            query, keep_blank_values=True, max_num_fields=160))
    except ValueError:
        pass
    return tuple(sorted(k for k in names if _FIELD.fullmatch(k)))[:_MAX_FIELDS]


def _mime_family(raw: Any) -> str:
    """Enum only: never echo a MIME header, boundary or arbitrary filename."""
    if not isinstance(raw, str):
        return "unknown"
    mime = raw.lower().split(";", 1)[0].strip()
    if mime == "application/x-www-form-urlencoded":
        return "form"
    if mime == "application/json" or mime.endswith("+json"):
        return "json"
    if mime == "multipart/form-data":
        return "multipart"
    if mime in ("text/html", "application/xhtml+xml"):
        return "html"
    if mime.startswith("text/"):
        return "text"
    return "unknown"


def _body_keys(data: Any) -> tuple[str, ...]:
    if not isinstance(data, dict):
        return ()
    names: set[str] = set()
    params = data.get("params")
    if isinstance(params, list):
        names.update(
            p["name"] for p in params[:160]
            if isinstance(p, dict) and isinstance(p.get("name"), str)
        )
    text = data.get("text")
    if isinstance(text, str) and len(text) <= _MAX_TEXT:
        family = _mime_family(data.get("mimeType", ""))
        if family == "json" and text.lstrip().startswith("{"):
            try:
                names.update(_safe_keys(json.loads(text)))
            except (ValueError, RecursionError):
                pass
        elif family == "form":
            try:
                names.update(k for k, _ in parse_qsl(
                    text, keep_blank_values=True, max_num_fields=160))
            except ValueError:
                pass
    return tuple(sorted(k for k in names if _FIELD.fullmatch(k)))[:_MAX_FIELDS]


def _response_text(content: Any) -> str | None:
    if not isinstance(content, dict):
        return None
    raw = content.get("text")
    if not isinstance(raw, str) or len(raw) > _MAX_TEXT * 2:
        return None
    if content.get("encoding") == "base64":
        try:
            decoded = base64.b64decode(raw, validate=True)
            if len(decoded) > _MAX_TEXT:
                return None
            return decoded.decode("utf-8-sig", "strict")
        except (binascii.Error, UnicodeError, ValueError):
            return None
    if content.get("encoding") not in (None, ""):
        return None
    return raw if len(raw) <= _MAX_TEXT else None


def _response_shape(content: Any) -> tuple[tuple[str, ...], tuple[str, ...], str, str]:
    text = _response_text(content)
    if text is None:
        return (), (), "unknown", "unknown"
    if not text.strip():
        return (), (), "empty", "none"
    try:
        obj = json.loads(text)
    except (ValueError, RecursionError):
        if text.lstrip().lower().startswith(("<html", "<!doctype html")):
            return (), (), "html", "none"
        return (), (), "unknown", "unknown"
    if isinstance(obj, list):
        return (), (), "json_array", "none"
    if not isinstance(obj, dict):
        return (), (), "json_scalar", "none"
    result = obj.get("result")
    if isinstance(result, str) and result.lstrip().startswith(("{", "[")):
        try:
            result = json.loads(result)
        except (ValueError, RecursionError):
            pass
    result_shape = (
        "object" if isinstance(result, dict)
        else "array" if isinstance(result, list)
        else "null" if result is None
        else "scalar"
    )
    return _safe_keys(obj), _safe_keys(result), "json_object", result_shape


@dataclass(frozen=True)
class ContractSampleSummary:
    endpoint: str
    request_count: int = 0
    request_fields: tuple[str, ...] = ()
    response_fields: tuple[str, ...] = ()
    result_fields: tuple[str, ...] = ()
    response_bodies_seen: int = 0
    http_codes: tuple[int, ...] = ()
    query_fields: tuple[str, ...] = ()
    request_formats: tuple[str, ...] = ()
    response_shapes: tuple[str, ...] = ()
    result_shapes: tuple[str, ...] = ()

    @property
    def captured(self) -> bool:
        return self.request_count > 0

    @property
    def has_request_contract(self) -> bool:
        return bool(self.request_fields or self.query_fields)

    @property
    def evidence_state(self) -> str:
        """A schema sample is not an accepted action or permission to submit."""
        if not self.captured:
            return "missing_capture"
        if not self.has_request_contract:
            return "missing_request_fields"
        if not self.response_fields and not self.response_shapes:
            return "missing_response_shape"
        if not self.response_fields and all(
            shape in ("unknown", "html", "empty") for shape in self.response_shapes
        ):
            return "missing_structured_response"
        return "schema_only"


@dataclass(frozen=True)
class TwoEndpointContractReport:
    code: str
    samples: tuple[ContractSampleSummary, ...]

    @property
    def message(self) -> str:
        if self.code != "parsed":
            return "文件不是有效且大小符合要求的 HAR；未从中提取接口。"
        states = {
            "missing_capture": "缺少正常使用时的对应 POST 请求样本",
            "missing_request_fields": "已见接口地址，缺少请求参数结构",
            "missing_response_shape": "已见请求参数，缺少响应结构",
            "missing_structured_response": "返回了内容，但缺少可识别的业务响应结构",
            "schema_only": "请求及响应字段结构已捕获，仍未验证业务语义",
        }
        sections = []
        for item in self.samples:
            kind = "轮次初始化" if item.endpoint == INIT_PATH else "教学班可选性预检"
            sections.append(
                f"{kind}：匹配 {item.request_count} 条请求；"
                f"HTTP {', '.join(str(x) for x in item.http_codes) or '未知'}\n"
                f"  URL 参数名：{', '.join(item.query_fields) or '无'}\n"
                f"  POST 字段名：{', '.join(item.request_fields) or '未获得'}\n"
                f"  请求类型：{', '.join(item.request_formats) or '未知'}；"
                f"响应结构：{', '.join(item.response_shapes) or '未知'}；"
                f"result 类型：{', '.join(item.result_shapes) or '未知'}\n"
                f"  响应外层字段名：{', '.join(item.response_fields) or '未获得'}\n"
                f"  result 字段名：{', '.join(item.result_fields) or '未获得'}\n"
                f"  下一步：{states[item.evidence_state]}"
            )
        return (
            "\n\n".join(sections)
            + "\n\n只报告字段名和结构，不导出任何请求/响应值。"
              "HTTP 200 不是业务成功；仍需人工核实取值来源、"
              "允许的访问方式、成功与失败样例。没有调用学校接口。"
        )


def inspect_two_endpoint_contracts(raw: bytes) -> TwoEndpointContractReport:
    if not isinstance(raw, bytes) or not raw or len(raw) > _MAX_FILE:
        return TwoEndpointContractReport("invalid", ())
    try:
        parsed = json.loads(raw.decode("utf-8-sig"))
        if not isinstance(parsed, dict):
            raise ValueError("invalid document")
        log = parsed.get("log", {})
        if not isinstance(log, dict):
            raise ValueError("invalid log")
        entries = log.get("entries", [])
        if not isinstance(entries, list) or len(entries) > _MAX_ENTRIES:
            raise ValueError("invalid entries")
    except (ValueError, TypeError, RecursionError, UnicodeError):
        return TwoEndpointContractReport("invalid", ())

    samples: list[ContractSampleSummary] = []
    for endpoint in KNOWN:
        count = 0
        request_names: set[str] = set()
        query_names: set[str] = set()
        response_names: set[str] = set()
        result_names: set[str] = set()
        codes: set[int] = set()
        request_formats: set[str] = set()
        response_shapes: set[str] = set()
        result_shapes: set[str] = set()
        response_bodies = 0
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            req, resp = entry.get("request"), entry.get("response")
            if not isinstance(req, dict):
                continue
            try:
                loc = urlsplit(req.get("url", ""))
            except (TypeError, ValueError):
                continue
            if (loc.scheme != "https" or loc.netloc != HOST
                    or loc.path != endpoint or req.get("method") != "POST"):
                continue
            count += 1
            if count > 100:
                break
            query_names.update(_query_keys(req, loc.query))
            request_names.update(_body_keys(req.get("postData")))
            if isinstance(req.get("postData"), dict):
                request_formats.add(
                    _mime_family(req["postData"].get("mimeType")))
            if isinstance(resp, dict):
                status = resp.get("status")
                if type(status) is int and 100 <= status <= 599:
                    codes.add(status)
                outer, nested, shape, result_shape = _response_shape(
                    resp.get("content"))
                response_shapes.add(shape)
                result_shapes.add(result_shape)
                if outer or shape in ("json_object", "json_array"):
                    response_bodies += 1
                response_names.update(outer)
                result_names.update(nested)

        samples.append(ContractSampleSummary(
            endpoint=endpoint, request_count=min(count, 100),
            request_fields=tuple(sorted(request_names))[:_MAX_FIELDS],
            response_fields=tuple(sorted(response_names))[:_MAX_FIELDS],
            result_fields=tuple(sorted(result_names))[:_MAX_FIELDS],
            response_bodies_seen=response_bodies,
            http_codes=tuple(sorted(codes)),
            query_fields=tuple(sorted(query_names))[:_MAX_FIELDS],
            request_formats=tuple(sorted(request_formats)),
            response_shapes=tuple(sorted(response_shapes)),
            result_shapes=tuple(sorted(result_shapes)),
        ))
    return TwoEndpointContractReport("parsed", tuple(samples))
