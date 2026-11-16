"""Evidence-driven attestation registry for the four enrollment capability gates.

The engine refuses to write unless a gateway *declares* a capability and a
local registry can *prove* it against a committed evidence document. A bare
``True`` on the gateway is therefore no longer sufficient: it must be paired
with an attestation whose evidence file exists, whose sha256 still matches,
and whose validity has not expired.

This replaces the previous dead end where no code path could ever set
``official_write_verified`` to ``True`` and every live run was impossible.

Nothing here performs network access, and no personal data is stored: an
attestation only references a repository document plus its digest.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Mapping

REGISTRY_VERSION = 1
REGISTRY_FILENAME = "attestations.json"

#: The four capability gates the enrollment engine requires.
GATE_NAMES: tuple[str, ...] = (
    "official_catalog_verified",
    "official_enrollment_verified",
    "official_window_verified",
    "official_write_verified",
)

#: A capture timestamp may not be further in the future than this tolerance,
#: which only absorbs ordinary clock drift between two machines.
_MAX_FUTURE_SKEW = timedelta(minutes=10)

_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_GATE_LABELS = {
    "official_catalog_verified": "官方选课目录",
    "official_enrollment_verified": "官方已选记录",
    "official_window_verified": "官方选课窗口状态",
    "official_write_verified": "官方提交写入合同",
}


class AttestationError(RuntimeError):
    """An attestation is missing, unreadable, tampered with or expired."""


def _parse_moment(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise AttestationError(f"{label}时间缺失")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as exc:
        raise AttestationError(f"{label}时间格式无效") from exc
    if moment.tzinfo is None:
        raise AttestationError(f"{label}时间必须带时区")
    return moment.astimezone(timezone.utc)


def _utc(moment: datetime) -> datetime:
    return moment.astimezone(timezone.utc)


def _safe_relative(root: Path, evidence: str) -> Path:
    """Resolve an evidence path strictly inside ``root``; never escape it."""
    if not isinstance(evidence, str) or not evidence.strip():
        raise AttestationError("验收证据路径缺失")
    if os.path.isabs(evidence) or evidence.startswith(("/", "\\")):
        raise AttestationError("验收证据必须是仓库内相对路径")
    candidate = (root / evidence).resolve()
    base = root.resolve()
    if candidate != base and base not in candidate.parents:
        raise AttestationError("验收证据路径越出工作目录")
    return candidate


@dataclass(frozen=True, slots=True)
class Attestation:
    """A single gate's evidence record."""

    gate: str
    evidence: str
    contract_sha256: str
    captured_at: datetime
    expires_at: datetime | None
    reviewer: str
    note: str = ""

    def __post_init__(self) -> None:
        if self.gate not in GATE_NAMES:
            raise AttestationError(f"未知的验收门禁：{self.gate}")
        if (not isinstance(self.reviewer, str) or not self.reviewer.strip()
                or len(self.reviewer) > 100):
            raise AttestationError("验收人标识无效")
        if not isinstance(self.note, str) or len(self.note) > 500:
            raise AttestationError("验收备注无效")
        if not isinstance(self.contract_sha256, str) or not _SHA256.match(self.contract_sha256):
            raise AttestationError("验收证据摘要必须是 64 位小写 sha256")
        if not isinstance(self.captured_at, datetime) or self.captured_at.tzinfo is None:
            raise AttestationError("验收时间必须带时区")
        if (self.expires_at is not None
                and (not isinstance(self.expires_at, datetime) or self.expires_at.tzinfo is None)):
            raise AttestationError("验收有效期必须带时区")
        if self.expires_at is not None and _utc(self.expires_at) <= _utc(self.captured_at):
            raise AttestationError("验收有效期必须晚于验收时间")

    @property
    def label(self) -> str:
        return _GATE_LABELS.get(self.gate, self.gate)

    def expired(self, now: datetime | None = None) -> bool:
        if self.expires_at is None:
            return False
        return _utc(self.expires_at) <= _utc(now or datetime.now(timezone.utc))

    def to_mapping(self) -> dict:
        return {
            "gate": self.gate,
            "evidence": self.evidence,
            "contract_sha256": self.contract_sha256,
            "captured_at": _utc(self.captured_at).isoformat(),
            "expires_at": _utc(self.expires_at).isoformat() if self.expires_at else None,
            "reviewer": self.reviewer,
            "note": self.note,
        }

    @classmethod
    def from_mapping(cls, value: object) -> "Attestation":
        if not isinstance(value, dict):
            raise AttestationError("验收登记项格式无效")
        gate = value.get("gate")
        evidence = value.get("evidence")
        reviewer = value.get("reviewer")
        digest = value.get("contract_sha256")
        note = value.get("note", "")
        if not isinstance(gate, str) or gate not in GATE_NAMES:
            raise AttestationError("验收登记项缺少合法门禁名")
        if not isinstance(evidence, str) or not evidence.strip():
            raise AttestationError("验收登记项缺少证据路径")
        if not isinstance(reviewer, str) or not reviewer.strip():
            raise AttestationError("验收登记项缺少验收人")
        if not isinstance(digest, str) or not _SHA256.match(digest):
            raise AttestationError("验收登记项摘要无效")
        if not isinstance(note, str):
            raise AttestationError("验收登记项备注无效")
        captured = _parse_moment(value.get("captured_at"), "验收")
        raw_expiry = value.get("expires_at")
        expiry = None if raw_expiry in (None, "") else _parse_moment(raw_expiry, "有效期")
        return cls(gate=gate, evidence=evidence, contract_sha256=digest,
                   captured_at=captured, expires_at=expiry,
                   reviewer=reviewer, note=note)


def hash_evidence(path: Path) -> str:
    """Stream a document once; large acceptance notes must not be loaded whole."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class AttestationRegistry:
    """Locally stored, hash-verified gate attestations.

    ``root`` is the repository (or app data) directory that evidence paths are
    resolved against. Verification is pure local I/O and never contacts a school.
    """

    entries: Mapping[str, Attestation] = field(default_factory=dict)
    root: Path = field(default_factory=Path.cwd)

    def __post_init__(self) -> None:
        if not isinstance(self.root, Path):
            raise AttestationError("验收登记表根目录无效")
        for gate, item in self.entries.items():
            if gate not in GATE_NAMES:
                raise AttestationError(f"验收登记表包含未知门禁：{gate}")
            if not isinstance(item, Attestation) or item.gate != gate:
                raise AttestationError("验收登记表条目与门禁名不一致")

    # -- construction ------------------------------------------------------

    @classmethod
    def load(cls, path: Path, root: Path | None = None) -> "AttestationRegistry":
        """Read ``attestations.json``. A missing file yields an EMPTY registry.

        An empty registry is not an error at load time; :meth:`require` is what
        fails closed, so the GUI can still show "0/4 verified" instead of
        crashing during startup.
        """
        file_path = Path(path)
        base = Path(root) if root is not None else file_path.parent
        if not file_path.exists():
            return cls(entries={}, root=base)
        if not file_path.is_file() or file_path.stat().st_size > 256 * 1024:
            raise AttestationError("验收登记表不可读或体积异常")
        try:
            document = json.loads(file_path.read_text(encoding="utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise AttestationError("验收登记表不是合法 JSON") from exc
        return cls.from_document(document, base)

    @classmethod
    def from_document(cls, document: object, root: Path) -> "AttestationRegistry":
        if not isinstance(document, dict) or document.get("version") != REGISTRY_VERSION:
            raise AttestationError("验收登记表版本无效")
        raw = document.get("attestations")
        if not isinstance(raw, list):
            raise AttestationError("验收登记表缺少 attestations 列表")
        entries: dict[str, Attestation] = {}
        for item in raw:
            attestation = Attestation.from_mapping(item)
            if attestation.gate in entries:
                raise AttestationError(f"同一门禁重复登记：{attestation.gate}")
            entries[attestation.gate] = attestation
        return cls(entries=entries, root=Path(root))

    def to_document(self) -> dict:
        return {
            "version": REGISTRY_VERSION,
            "attestations": [self.entries[gate].to_mapping()
                             for gate in GATE_NAMES if gate in self.entries],
        }

    def save(self, path: Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(self.to_document(), ensure_ascii=False, indent=2) + "\n"
        temporary = target.with_name(target.name + ".tmp")
        temporary.write_text(encoded, encoding="utf-8")
        os.replace(temporary, target)

    def with_entry(self, attestation: Attestation) -> "AttestationRegistry":
        merged = dict(self.entries)
        merged[attestation.gate] = attestation
        return AttestationRegistry(entries=merged, root=self.root)

    def without_entry(self, gate: str) -> "AttestationRegistry":
        merged = {key: value for key, value in self.entries.items() if key != gate}
        return AttestationRegistry(entries=merged, root=self.root)

    # -- issuing -----------------------------------------------------------

    @classmethod
    def issue(cls, root: Path, *, gate: str, evidence: str, reviewer: str,
              expires_at: datetime | None = None, note: str = "",
              captured_at: datetime | None = None) -> Attestation:
        """Record a gate against a real evidence document.

        The document must already exist. Its digest is captured now, so any
        later edit to the evidence invalidates the attestation until reissued.
        """
        base = Path(root)
        document = _safe_relative(base, evidence)
        if not document.is_file():
            raise AttestationError(f"验收证据文件不存在：{evidence}")
        return Attestation(
            gate=gate,
            evidence=evidence,
            contract_sha256=hash_evidence(document),
            captured_at=captured_at or datetime.now(timezone.utc),
            expires_at=expires_at,
            reviewer=reviewer,
            note=note,
        )

    # -- verification ------------------------------------------------------

    def verify(self, gate: str, *, now: datetime | None = None,
               rehash: bool = True) -> Attestation:
        """Return the valid attestation for ``gate`` or raise."""
        if gate not in GATE_NAMES:
            raise AttestationError(f"未知的验收门禁：{gate}")
        attestation = self.entries.get(gate)
        if attestation is None:
            raise AttestationError(f"{_GATE_LABELS[gate]}尚未登记验收证据")
        moment = _utc(now or datetime.now(timezone.utc))
        if _utc(attestation.captured_at) - moment > _MAX_FUTURE_SKEW:
            raise AttestationError(f"{_GATE_LABELS[gate]}验收时间在未来")
        if attestation.expired(moment):
            raise AttestationError(f"{_GATE_LABELS[gate]}验收已过期，需重新取证")
        if rehash:
            document = _safe_relative(self.root, attestation.evidence)
            if not document.is_file():
                raise AttestationError(f"{_GATE_LABELS[gate]}验收证据文件已丢失")
            if hash_evidence(document) != attestation.contract_sha256:
                raise AttestationError(f"{_GATE_LABELS[gate]}验收证据已被修改，需重新登记")
        return attestation

    def require(self, gate: str) -> Attestation:
        """Engine-facing alias; identical fail-closed semantics."""
        return self.verify(gate)

    def missing(self) -> tuple[str, ...]:
        return tuple(gate for gate in GATE_NAMES if gate not in self.entries)

    def describe(self, *, now: datetime | None = None) -> dict:
        """Privacy-safe summary for CLI/GUI display (no file contents)."""
        moment = _utc(now or datetime.now(timezone.utc))
        rows = []
        for gate in GATE_NAMES:
            attestation = self.entries.get(gate)
            if attestation is None:
                rows.append({"gate": gate, "label": _GATE_LABELS[gate],
                             "state": "missing", "evidence": "", "expires_at": None})
                continue
            try:
                self.verify(gate, now=moment)
                state = "valid"
            except AttestationError as error:
                state = "expired" if attestation.expired(moment) else "invalid"
                rows.append({"gate": gate, "label": _GATE_LABELS[gate],
                             "state": state, "evidence": attestation.evidence,
                             "expires_at": (attestation.expires_at.isoformat()
                                            if attestation.expires_at else None),
                             "reason": str(error)})
                continue
            rows.append({"gate": gate, "label": _GATE_LABELS[gate],
                         "state": state, "evidence": attestation.evidence,
                         "expires_at": (attestation.expires_at.isoformat()
                                        if attestation.expires_at else None)})
        return {
            "total": len(GATE_NAMES),
            "valid": sum(1 for row in rows if row["state"] == "valid"),
            "rows": rows,
        }

    def all_valid(self, *, now: datetime | None = None) -> bool:
        try:
            for gate in GATE_NAMES:
                self.verify(gate, now=now)
        except AttestationError:
            return False
        return True


def default_registry_path(app_dir: Path) -> Path:
    return Path(app_dir) / REGISTRY_FILENAME


def resolve_registry(raw: object, *, app_dir: Path = Path.cwd(),
                     repo_root: Path | None = None) -> AttestationRegistry:
    """Build a registry from the ``attestation:`` config section.

    ``raw`` may be ``None`` (no section: auto-detect ``attestations.json`` next
    to the application data), a string path, or a mapping with ``path`` and
    ``root`` keys. Evidence paths resolve against ``root``, which defaults to
    the caller-provided repository root.
    """
    base = Path(repo_root) if repo_root is not None else Path(app_dir)
    if raw is None:
        return AttestationRegistry.load(default_registry_path(app_dir), base)
    if isinstance(raw, str):
        path, root = raw, base
    elif isinstance(raw, dict):
        path = raw.get("path") or str(default_registry_path(app_dir))
        root = Path(raw["root"]) if raw.get("root") else base
    else:
        raise AttestationError("attestation 配置段格式无效")
    return AttestationRegistry.load(Path(path), root)


def expiry_after(days: int, *, start: datetime | None = None) -> datetime:
    if type(days) is not int or not 1 <= days <= 3650:
        raise AttestationError("验收有效期天数需为 1–3650")
    return (start or datetime.now(timezone.utc)) + timedelta(days=days)
