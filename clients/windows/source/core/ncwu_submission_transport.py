"""Single-shot S202 write transport, deliberately separate from enrollment policy.

The endpoint was observed in the owner's NCWU interface inventory.
The full, *successful* per-class item encoding is not yet proven. This module
therefore NEVER constructs an "items" value or guesses hidOption; a future
official-form adapter must supply a same-session, independently attested form.

Only the V5.1 EnrollmentEngine may request writes after durable intent and
all capability attestations. A transport receipt is never proof of enrollment.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Callable, Mapping

import httpx

from core.attestation import AttestationRegistry, GATE_NAMES
from core.client import encode_gbk_form
from core.enrollment import EnrollmentNotReady, SubmitReceipt
from core.ncwu_live_elective import ORIGIN, REFERER, _validate_origin
from core.ncwu_read_support import default_read_client, set_scoped_cookie

SUBMIT_PATH = "/hsjw/STU_ElectCourseAction.do"
_CONTENT_TYPE = "application/x-www-form-urlencoded; charset=GBK"
_REQUIRED = ("xh", "xn", "xq", "lcid", "xktype", "hidOption", "items")


@dataclass(frozen=True, slots=True)
class PreparedNCWUSubmission:
    """An externally VERIFIED first-party write form, not a captured cookie.

    item_binding_sha256 is the hash of the separate reviewed mapping evidence.
    It never substitutes for the V5.1 attestation or official readback.
    This value is an audit reference; the form fields themselves stay in RAM.
    """

    fields: tuple[tuple[str, str], ...]
    internal_student_id: str
    academic_year: str
    academic_term: str
    round_id: str
    selected_item: str
    item_binding_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.fields, tuple) or not 6 <= len(self.fields) <= 100:
            raise ValueError("选课表单字段数量不符合当前官网契约")
        names = []
        values = {}
        for pair in self.fields:
            if (not isinstance(pair, tuple) or len(pair) != 2
                    or any(not isinstance(part, str) for part in pair)):
                raise ValueError("选课表单必须是原站字段的有序键值对")
            key, value = pair
            if (not key.isascii() or not key.replace("_", "").isalnum()
                    or len(key) > 64 or len(value) > 1024 or "\\x00" in value):
                raise ValueError("选课字段名或值异常")
            names.append(key)
            values[key] = value
        if len(set(names)) != len(names):
            raise ValueError("重复字段可能导致提交目标不一致")
        if any(not values.get(name) for name in _REQUIRED):
            raise ValueError("缺少同一会话的正式选课字段")
        if values["xktype"] != "2":
            raise ValueError("非 S202 正选类型，不发送")
        for field, known in (
            ("xh", self.internal_student_id),
            ("xn", self.academic_year),
            ("xq", self.academic_term),
            ("lcid", self.round_id),
            ("items", self.selected_item),
        ):
            if not isinstance(known, str) or not known or values[field] != known:
                raise ValueError("字段与已核验的本人会话或选课项不一致")
        # A multi-course or partially constructed "items" packet is not a
        # one-target attempt. The observed multi-item delimiter is '|'.
        if "|" in self.selected_item or len(self.selected_item) > 512:
            raise ValueError("禁止用未知格式一次提交多个课程")
        if (not isinstance(self.item_binding_sha256, str)
                or len(self.item_binding_sha256) != 64
                or any(c not in "0123456789abcdef" for c in self.item_binding_sha256)):
            raise ValueError("教学班与选课项的映射证据缺失")


async def submit_prepared_s202_once(
    config: Mapping, *,
    session_cookie: str | None,
    prepared: PreparedNCWUSubmission,
    current_internal_student_id: str,
    current_academic_year: str,
    current_academic_term: str,
    current_round_id: str,
    item_binding_evidence: bytes,
    attestations: AttestationRegistry | None,
    user_opted_in: bool,
    institution_permits_write: bool,
    request_budget,
    http_factory: Callable[[], httpx.AsyncClient] = default_read_client,
) -> SubmitReceipt:
    """At most ONE HTTP POST. Never retry, infer success, or read browser cookies.

    The future NCWU adapter must derive prepared.fields from the currently
    authenticated official selection action (not from the 2568 table text).
    EnrollmentEngine must reserve its intent *before* calling this function.
    The caller must read exact official course+class records after the POST.
    """
    if not isinstance(prepared, PreparedNCWUSubmission):
        raise EnrollmentNotReady("尚无经官网证实的单教学班提交表单")
    if user_opted_in is not True or institution_permits_write is not True:
        raise EnrollmentNotReady("用户授权或学校自动写入许可未核实")
    if attestations is None:
        raise EnrollmentNotReady("缺少四项可复核的协议验收记录")
    for name in GATE_NAMES:
        attestations.require(name)
    if not _validate_origin(config, session_cookie):
        raise EnrollmentNotReady("仅允许 EXE 当前的华水本人 HTTPS 会话")
    for current, expected in (
        (current_internal_student_id, prepared.internal_student_id),
        (current_academic_year, prepared.academic_year),
        (current_academic_term, prepared.academic_term),
        (current_round_id, prepared.round_id),
    ):
        if not isinstance(current, str) or current != expected:
            raise EnrollmentNotReady("会话、学期或选课轮次发生变化，停止写入")
    if (not isinstance(item_binding_evidence, bytes)
            or sha256(item_binding_evidence).hexdigest() != prepared.item_binding_sha256):
        raise EnrollmentNotReady("当前教学班与 items 的原始证据不一致")
    if request_budget is None or not callable(getattr(request_budget, "request", None)):
        raise EnrollmentNotReady("学校请求预算尚未独立验证")

    headers = {
        "Referer": REFERER,
        "Accept": "text/html,text/plain",
        "Content-Type": _CONTENT_TYPE,
    }
    body = encode_gbk_form(list(prepared.fields))
    # No HTTP retry, no redirect and no proxy inherited from the operating
    # system. The durable enrollment journal owns idempotency and recovery.
    async with http_factory() as client:
        set_scoped_cookie(client, session_cookie)
        async with request_budget.request():
            response = await client.post(
                ORIGIN + SUBMIT_PATH,
                content=body,
                headers=headers,
                follow_redirects=False,
            )
    if response.status_code != 200:
        raise EnrollmentNotReady("学校提交响应异常；发送结果未知，需回读，不得重发")
    if not response.content or len(response.content) > 128 * 1024:
        raise EnrollmentNotReady("提交响应为空或过大；不能判定选课成功")
    # Even a 'flag:1' / '操作成功' response can be a false positive.
    # Only EnrollmentEngine's exact official course+class readback may confirm.
    return SubmitReceipt(None)
