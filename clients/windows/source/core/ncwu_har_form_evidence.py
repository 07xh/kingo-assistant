"""Bridge: one captured successful S202 POST -> a reviewable form observation.

``tools/patch/p0_protocol_recon.py`` turns a HAR into a *sanitized* evidence
file: hashes only, never the plaintext ``items`` / ``hidOption`` / ``xh``. That
file proves what was captured but deliberately cannot reconstruct the form.

This module closes the other half. It reads the SAME HAR, pulls the single
successful ``STU_ElectCourseAction.do`` POST out of it, and cross-checks every
hashed value in the evidence file against the live bytes:

* ``items``          sha256 must equal ``items.value_sha256``
* ``hidOption``      sha256 must equal ``form.hid_option_sha256``
* ``xh``             sha256 must equal ``form.student_id_sha256``
* ``xn``/``xq``/``lcid`` must equal the evidence and the live session scope
* the bound internal teaching class must equal the requested target

Only when all of that holds does it emit a ``CurrentS202FormObservation`` that
``VerifiedS202FormProvider`` will accept. Anything ambiguous fails closed.

Nothing here performs network I/O, guesses ``items``, or fabricates a binding.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

from core.course_models import CourseTarget
from core.ncwu_submission_transport import SUBMIT_PATH
from core.ncwu_verified_form_provider import CurrentS202FormObservation
from core.ncwu_verified_readback_bridge import ReadbackScope

#: Gate name owned by ``attestation``; kept as a literal so this module does not
#: import the registry just to compare a string.
WRITE_GATE = "official_write_verified"

#: The seven fields a real S202 write form must carry in one session.
REQUIRED_FIELDS = ("xh", "xn", "xq", "lcid", "xktype", "hidOption", "items")

MAX_EVIDENCE_BYTES = 1024 * 1024
_MAX_HAR_BYTES = 256 * 1024 * 1024


class FormEvidenceError(ValueError):
    """The HAR and the evidence file do not describe the same write."""


def _sha256_text(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _is_hex64(value: str) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(ch in "0123456789abcdef" for ch in value))


# ---------------------------------------------------------------------------
# Evidence file
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class ItemsBinding:
    """Sanitized proof that one ``items`` value maps to one internal class."""

    raw: bytes
    course_id: str
    class_id: str
    skbjdm: str
    skbh: str
    matched_field: str
    match_strength: str
    items_sha256: str
    items_length: int
    round_id: str
    academic_year: str
    academic_term: str
    hid_option_sha256: str
    student_id_sha256: str
    source_path: str

    @property
    def digest(self) -> str:
        return sha256(self.raw).hexdigest()

    def as_dict(self) -> dict:
        return {
            "course_id": self.course_id, "class_id": self.class_id,
            "skbjdm": self.skbjdm, "skbh": self.skbh,
            "matched_field": self.matched_field,
            "match_strength": self.match_strength,
            "items_sha256": self.items_sha256,
            "items_length": self.items_length,
            "source_path": self.source_path,
        }


def load_items_binding(path: Path | str) -> ItemsBinding:
    """Read ``evidence/items_binding.json`` and reject anything unproven."""
    source = Path(path)
    try:
        raw = source.read_bytes()
    except OSError as exc:
        raise FormEvidenceError(f"无法读取映射证据：{source}") from exc
    if not 1 <= len(raw) <= MAX_EVIDENCE_BYTES:
        raise FormEvidenceError("映射证据大小异常")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise FormEvidenceError("映射证据不是有效 JSON") from exc
    if not isinstance(payload, dict):
        raise FormEvidenceError("映射证据结构无效")
    if payload.get("version") != 1:
        raise FormEvidenceError("映射证据版本不受支持")
    if payload.get("gate") != WRITE_GATE:
        raise FormEvidenceError(f"映射证据的 gate 不是 {WRITE_GATE}")
    if payload.get("mapping_proven") is not True:
        raise FormEvidenceError("映射证据未标记为已证实（mapping_proven != true）")
    binding = payload.get("bound_teaching_class")
    if not isinstance(binding, dict):
        raise FormEvidenceError("映射证据缺少 bound_teaching_class")
    if binding.get("match_strength") != "high":
        # skbh is a page-visible number, not the internal class id. A low
        # strength match cannot authorise a write.
        raise FormEvidenceError("教学班与选课项仅为弱匹配（skbh），不足以授权写入")
    form = payload.get("form") if isinstance(payload.get("form"), dict) else {}
    items = payload.get("items") if isinstance(payload.get("items"), dict) else {}
    source_block = payload.get("source") if isinstance(payload.get("source"), dict) else {}

    course_id = str(binding.get("course_id") or "").strip()
    class_id = str(binding.get("class_id") or "").strip()
    if not course_id or not class_id:
        raise FormEvidenceError("映射证据缺少课程代码或内部教学班 ID")
    items_sha = str(items.get("value_sha256") or "")
    if not _is_hex64(items_sha):
        raise FormEvidenceError("映射证据的 items 哈希无效")
    hid_sha = str(form.get("hid_option_sha256") or "")
    if not _is_hex64(hid_sha):
        raise FormEvidenceError("映射证据的 hidOption 哈希无效")
    student_sha = str(form.get("student_id_sha256") or "")
    if not _is_hex64(student_sha):
        raise FormEvidenceError("映射证据的学号哈希无效")
    if items.get("contains_pipe") is True:
        raise FormEvidenceError("映射证据记录的多课程 items 不支持单次提交")
    length = items.get("length")
    if type(length) is not int or not 1 <= length <= 512:
        raise FormEvidenceError("映射证据的 items 长度越界")
    if str(form.get("xktype") or "") != "2":
        raise FormEvidenceError("映射证据的 xktype 不是 S202 正选（2）")
    return ItemsBinding(
        raw=raw,
        course_id=course_id,
        class_id=class_id,
        skbjdm=str(binding.get("skbjdm") or ""),
        skbh=str(binding.get("skbh") or ""),
        matched_field=str(binding.get("matched_field") or ""),
        match_strength=str(binding.get("match_strength") or ""),
        items_sha256=items_sha,
        items_length=length,
        round_id=str(form.get("round_id") or ""),
        academic_year=str(form.get("academic_year") or ""),
        academic_term=str(form.get("academic_term") or ""),
        hid_option_sha256=hid_sha,
        student_id_sha256=student_sha,
        source_path=str(source_block.get("path") or ""),
    )


# ---------------------------------------------------------------------------
# HAR extraction
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True, repr=False)
class RawSubmitForm:
    """The plaintext write form as captured. Never logged, never persisted."""

    fields: tuple[tuple[str, str], ...]
    status: int
    source_path: str
    origin: str
    captured_at: str = ""

    def field(self, name: str) -> str:
        for key, value in self.fields:
            if key == name:
                return value
        return ""

    @property
    def items(self) -> str:
        return self.field("items")

    def __repr__(self) -> str:
        return f"RawSubmitForm(fields={len(self.fields)}, status={self.status})"


def _har_entries(document: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    log = document.get("log") if isinstance(document, Mapping) else None
    if not isinstance(log, Mapping):
        raise FormEvidenceError("HAR 缺少 log 段")
    entries = log.get("entries")
    if not isinstance(entries, list):
        raise FormEvidenceError("HAR 缺少 log.entries")
    for entry in entries:
        if isinstance(entry, Mapping):
            yield entry


def _request_params(entry: Mapping[str, Any]) -> list[tuple[str, str]]:
    request = entry.get("request")
    if not isinstance(request, Mapping):
        return []
    post_data = request.get("postData")
    pairs: list[tuple[str, str]] = []
    if isinstance(post_data, Mapping):
        params = post_data.get("params")
        if isinstance(params, list):
            for item in params:
                if not isinstance(item, Mapping):
                    continue
                name = item.get("name")
                value = item.get("value")
                if isinstance(name, str) and name:
                    pairs.append((name, value if isinstance(value, str) else ""))
        if not pairs:
            text = post_data.get("text")
            if isinstance(text, str) and text:
                pairs = _parse_form_text(text)
    return pairs


def _parse_form_text(text: str) -> list[tuple[str, str]]:
    """Parse ``a=1&b=2``; the real NCWU form is GBK-encoded on the wire."""
    from urllib.parse import parse_qsl

    for encoding in ("gbk", "utf-8"):
        try:
            decoded = text.encode("latin-1").decode(encoding)
        except (UnicodeEncodeError, UnicodeDecodeError):
            # Already a decoded str (Chrome writes UTF-8 JSON), so no re-decode
            # is possible; fall through to the raw value.
            decoded = text
        pairs = parse_qsl(decoded, keep_blank_values=True)
        if pairs:
            return [(name, value) for name, value in pairs]
    return []


def extract_submit_form(document: Mapping[str, Any]) -> RawSubmitForm:
    """Pick the single usable S202 write sample out of a HAR.

    Multiple distinct samples are ambiguous: replaying the wrong one writes the
    wrong class. Ambiguity raises instead of picking the first.
    """
    candidates: list[RawSubmitForm] = []
    for entry in _har_entries(document):
        request = entry.get("request")
        if not isinstance(request, Mapping):
            continue
        if str(request.get("method", "")).upper() != "POST":
            continue
        url = str(request.get("url") or "")
        if not url:
            continue
        split = urlsplit(url)
        if split.path != SUBMIT_PATH:
            continue
        response = entry.get("response") if isinstance(entry.get("response"), Mapping) else {}
        status = response.get("status")
        status_code = status if type(status) is int else 0
        pairs = _request_params(entry)
        if not pairs:
            continue
        fields = tuple(pairs)
        values = dict(fields)
        if not all(values.get(name) for name in REQUIRED_FIELDS):
            continue
        if values.get("xktype") != "2":
            continue
        candidates.append(RawSubmitForm(
            fields=fields,
            status=status_code,
            source_path=split.path,
            origin=f"{split.scheme}://{split.netloc}",
            captured_at=str(entry.get("startedDateTime") or ""),
        ))
    if not candidates:
        raise FormEvidenceError(
            f"HAR 中没有包含全部 {len(REQUIRED_FIELDS)} 个字段且 xktype=2 的 "
            f"{SUBMIT_PATH} POST 样本")
    distinct = {tuple(sorted(cand.fields)) for cand in candidates}
    if len(distinct) > 1:
        raise FormEvidenceError(
            f"HAR 中存在 {len(distinct)} 个不同的写表单样本，无法确定唯一目标")
    return candidates[0]


# ---------------------------------------------------------------------------
# Cross-checks + observation
# ---------------------------------------------------------------------------

def verify_pairing(form: RawSubmitForm, binding: ItemsBinding) -> dict:
    """Hashes-only proof that the HAR and the evidence describe ONE write.

    No session values are involved: this answers "did I edit the evidence file,
    or is this a different capture?" and is safe to run on a machine that is not
    logged in.
    """
    if form.source_path != SUBMIT_PATH:
        raise FormEvidenceError("HAR 样本不是已核实的 S202 提交路径")
    if not form.origin.startswith("https://"):
        raise FormEvidenceError("HAR 样本不是 HTTPS 来源")
    if binding.source_path and binding.source_path != form.source_path:
        raise FormEvidenceError("映射证据与 HAR 样本的提交路径不一致")

    items = form.items
    if not items or "|" in items or len(items) > 512:
        raise FormEvidenceError("items 为空、含多课程分隔符或超长")
    if _sha256_text(items) != binding.items_sha256:
        raise FormEvidenceError("items 与映射证据的哈希不一致（不是同一次提交）")
    if len(items) != binding.items_length:
        raise FormEvidenceError("items 长度与映射证据不一致")

    hid_option = form.field("hidOption")
    if not hid_option or _sha256_text(hid_option) != binding.hid_option_sha256:
        raise FormEvidenceError("hidOption 与映射证据的哈希不一致")

    student_id = form.field("xh")
    if not student_id or _sha256_text(student_id) != binding.student_id_sha256:
        raise FormEvidenceError("学号与映射证据的哈希不一致")

    for name, expected, label in (
        ("xn", binding.academic_year, "映射证据学年"),
        ("xq", binding.academic_term, "映射证据学期"),
        ("lcid", binding.round_id, "映射证据轮次"),
    ):
        if binding_value_present(expected) and form.field(name) != expected:
            raise FormEvidenceError(f"{name} 与{label}不一致")

    return {
        "course_id": binding.course_id,
        "class_id": binding.class_id,
        "matched_field": binding.matched_field,
        "match_strength": binding.match_strength,
        "items_length": len(items),
        "items_sha256": binding.items_sha256,
        "fields": [name for name, _ in form.fields],
        "evidence_sha256": binding.digest,
        "har_status": form.status,
    }


def cross_check(form: RawSubmitForm, binding: ItemsBinding,
                scope: ReadbackScope, target: CourseTarget) -> dict:
    """Every reason this pair is NOT the reviewed write, or a proof summary."""
    if form.origin != scope.school_origin:
        raise FormEvidenceError("HAR 样本来源与当前会话学校来源不一致")
    proof = verify_pairing(form, binding)

    for name, expected, label in (
        ("xh", scope.internal_student_id, "当前会话学号"),
        ("xn", scope.academic_year, "当前学年"),
        ("xq", scope.academic_term, "当前学期"),
        ("lcid", scope.round_id, "当前选课轮次"),
    ):
        actual = form.field(name)
        if not actual or actual != expected:
            raise FormEvidenceError(f"{name} 与{label}不一致，拒绝复用旧表单")

    if binding.course_id != target.course_id:
        raise FormEvidenceError("映射证据的课程代码与目标课程不一致")
    if binding.class_id != target.class_id:
        raise FormEvidenceError("映射证据的内部教学班与目标教学班不一致")
    if target.semester and target.semester != scope.semester:
        raise FormEvidenceError("目标学期与当前会话学期不一致")

    return proof


def binding_value_present(value: str) -> bool:
    """An empty string in older evidence files means "not recorded"."""
    return isinstance(value, str) and bool(value.strip())


def build_form_observation(
    *, form: RawSubmitForm, binding: ItemsBinding, scope: ReadbackScope,
    target: CourseTarget, observed_monotonic: float | None = None,
) -> CurrentS202FormObservation:
    """Produce the observation ``VerifiedS202FormProvider`` accepts.

    ``observed_monotonic`` must fall inside the provider's read window; pass
    ``None`` to stamp ``time.monotonic()`` at call time, or an explicit value
    when the caller already bracketed the read.
    """
    proof = cross_check(form, binding, scope, target)
    stamp = time.monotonic() if observed_monotonic is None else observed_monotonic
    if type(stamp) not in (int, float):
        raise FormEvidenceError("observed_monotonic 必须是数字")
    return CurrentS202FormObservation(
        scope=scope,
        source_path=form.source_path,
        course_code=proof["course_id"],
        internal_class_id=proof["class_id"],
        selected_item=form.items,
        fields=form.fields,
        binding_evidence=binding.raw,
        observed_monotonic=float(stamp),
    )


def load_har(path: Path | str) -> dict:
    """Read a HAR file with a size ceiling; the capture may hold a whole term."""
    source = Path(path)
    try:
        size = source.stat().st_size
    except OSError as exc:
        raise FormEvidenceError(f"无法读取 HAR：{source}") from exc
    if not 1 <= size <= _MAX_HAR_BYTES:
        raise FormEvidenceError("HAR 大小异常")
    try:
        return json.loads(source.read_text(encoding="utf-8", errors="replace"))
    except ValueError as exc:
        raise FormEvidenceError("HAR 不是有效 JSON") from exc


def prepare_from_files(
    *, har_path: Path | str, binding_path: Path | str, scope: ReadbackScope,
    target: CourseTarget, observed_monotonic: float | None = None,
) -> CurrentS202FormObservation:
    """One-call path: HAR file + evidence file -> reviewable observation."""
    binding = load_items_binding(binding_path)
    form = extract_submit_form(load_har(har_path))
    return build_form_observation(
        form=form, binding=binding, scope=scope, target=target,
        observed_monotonic=observed_monotonic)
