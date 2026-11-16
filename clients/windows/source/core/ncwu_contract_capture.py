"""Local, privacy-preserving HAR contract inventory for two NCWU read paths.

Only recognized URL paths and field *names* are emitted. No request or
response values, account identifiers, cookies, signatures, tokens or
unredacted example payloads leave the original local HAR. This is not an
HTTP client; in particular it cannot call the unverified initializer or
teaching-class eligibility endpoint on the university server.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any
from urllib.parse import parse_qsl, urlsplit

HOST = "jwmis.ncwu.edu.cn"
INIT_PATH = "/hsjw/STU_DynamicInitDataAction.do"
ELIGIBILITY_PATH = "/hsjw/jw/common/isSelectableSkbjdm.action"
KNOWN = (INIT_PATH, ELIGIBILITY_PATH)
_MAX_FILE = 16 * 1024 * 1024
_MAX_ENTRIES = 8000
_FIELD = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]{0,63}$")


def _safe_keys(obj: Any) -> tuple[str, ...]:
    if not isinstance(obj, dict):
        return ()
    return tuple(sorted(
        k for k in obj
        if isinstance(k, str) and _FIELD.fullmatch(k)
    ))[:100]


def _body_keys(data: Any) -> tuple[str, ...]:
    if not isinstance(data, dict):
        return ()
    params = data.get("params")
    if isinstance(params, list):
        names = {
            p.get("name") for p in params if isinstance(p, dict)
            and isinstance(p.get("name"), str)
        }
        return tuple(sorted(k for k in names if _FIELD.fullmatch(k)))[:100]
    text = data.get("text")
    if not isinstance(text, str) or len(text) > 262144:
        return ()
    mime = str(data.get("mimeType", "")).lower()
    if "json" in mime and text.lstrip().startswith("{"):
        try:
            return _safe_keys(json.loads(text))
        except (ValueError, RecursionError):
            return ()
    if "form-urlencoded" in mime:
        try:
            names = {k for k, _ in parse_qsl(
                text, keep_blank_values=True, strict_parsing=False,
                max_num_fields=160)}
            return tuple(sorted(k for k in names if _FIELD.fullmatch(k)))[:100]
        except ValueError:
            return ()
    return ()


def _response_keys(content: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if not isinstance(content, dict) or content.get("encoding") == "base64":
        return (), ()
    text = content.get("text")
    if not isinstance(text, str) or len(text) > 262144:
        return (), ()
    try:
        obj = json.loads(text)
        if not isinstance(obj, dict):
            return (), ()
        result = obj.get("result")
        if isinstance(result, str) and result.lstrip().startswith("{"):
            try:
                result = json.loads(result)
            except ValueError:
                result = None
        return _safe_keys(obj), _safe_keys(result)
    except (ValueError, RecursionError):
        return (), ()


@dataclass(frozen=True)
class ContractSampleSummary:
    endpoint: str
    request_count: int = 0
    request_fields: tuple[str, ...] = ()
    response_fields: tuple[str, ...] = ()
    result_fields: tuple[str, ...] = ()
    response_bodies_seen: int = 0
    http_codes: tuple[int, ...] = ()

    @property
    def captured(self) -> bool:
        return self.request_count > 0

    @property
    def has_request_contract(self) -> bool:
        return bool(self.request_fields)


@dataclass(frozen=True)
class TwoEndpointContractReport:
    code: str
    samples: tuple[ContractSampleSummary, ...]

    @property
    def message(self) -> str:
        if self.code != "parsed":
            return "文件不是有效且大小符合要求的 HAR；未从中提取接口。"
        parts = []
        for x in self.samples:
            kind = "轮次初始化" if x.endpoint == INIT_PATH else "教学班可选性预检"
            parts.append(
                f"{kind}：找到 {x.request_count} 条本人会话请求；"
                f"请求字段 {', '.join(x.request_fields) or '未获得'}；"
                f"响应外层字段 {', '.join(x.response_fields) or '未获得'}；"
                f"result 字段 {', '.join(x.result_fields) or '未获得'}；"
                f"HTTP {', '.join(str(s) for s in x.http_codes) or '未知'}。"
            )
        return (
            "\n".join(parts)
            + "\n\n仅报告字段名，未核实参数取值、账户权限、业务成功含义，"
              "未调用学校接口或开启自动提交。"
        )


def inspect_two_endpoint_contracts(raw: bytes) -> TwoEndpointContractReport:
    if not isinstance(raw, bytes) or not raw or len(raw) > _MAX_FILE:
        return TwoEndpointContractReport("invalid", ())
    try:
        obj = json.loads(raw.decode("utf-8-sig"))
        entries = obj.get("log", {}).get("entries", [])
        if not isinstance(entries, list) or len(entries) > _MAX_ENTRIES:
            raise ValueError("invalid entries")
    except (ValueError, TypeError, AttributeError, UnicodeError):
        return TwoEndpointContractReport("invalid", ())
    samples: list[ContractSampleSummary] = []
    for endpoint in KNOWN:
        count = 0
        request_names: set[str] = set()
        response_names: set[str] = set()
        result_names: set[str] = set()
        codes: set[int] = set()
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
            request_names.update(_body_keys(req.get("postData")))
            if isinstance(resp, dict):
                status = resp.get("status")
                if isinstance(status, int) and 100 <= status <= 599:
                    codes.add(status)
                outer, nested = _response_keys(resp.get("content"))
                if outer:
                    response_bodies += 1
                response_names.update(outer)
                result_names.update(nested)
        samples.append(ContractSampleSummary(
            endpoint=endpoint, request_count=min(count, 100),
            request_fields=tuple(sorted(request_names))[:100],
            response_fields=tuple(sorted(response_names))[:100],
            result_fields=tuple(sorted(result_names))[:100],
            response_bodies_seen=response_bodies,
            http_codes=tuple(sorted(codes)),
        ))
    return TwoEndpointContractReport("parsed", tuple(samples))
