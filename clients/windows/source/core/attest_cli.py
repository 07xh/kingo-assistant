"""Standalone CLI for the enrollment capability attestation registry.

Deliberately standard-library only: it must keep working when the optional GUI
dependencies (rich / tkinter / pystray) are unavailable, because arming or
revoking a write gate is an operational task, not an interactive one.

Usage
-----
    python -m core.attest_cli status
    python -m core.attest_cli issue --gate official_write_verified \\
        --evidence docs/acceptance/ncwu-write-contract.md \\
        --reviewer ishii --days 90 --note "one confirmed submission"
    python -m core.attest_cli revoke --gate official_write_verified
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

from core.attestation import (
    GATE_NAMES, AttestationError, AttestationRegistry, REGISTRY_FILENAME,
    expiry_after,
)

_STATE_LABEL = {
    "valid": "有效",
    "expired": "已过期",
    "invalid": "证据失效",
    "missing": "未登记",
}


def _default_registry(path: str | None, root: str | None) -> tuple[Path, Path]:
    registry_path = Path(path) if path else Path(REGISTRY_FILENAME)
    root_path = Path(root) if root else Path.cwd()
    return registry_path, root_path


def attest_status(registry_path: Path, root: Path) -> int:
    try:
        registry = AttestationRegistry.load(registry_path, root)
    except AttestationError as error:
        print(f"验收登记表不可用：{error}")
        return 2
    report = registry.describe()
    print(f"验收登记表：{registry_path}")
    print(f"证据根目录：{root}")
    print(f"已通过：{report['valid']}/{report['total']}\n")
    for row in report["rows"]:
        detail = row.get("reason") or row.get("evidence") or ""
        expires = row.get("expires_at") or "长期"
        print(f"  [{_STATE_LABEL.get(row['state'], row['state'])}] {row['gate']}")
        print(f"      {row['label']}｜有效期至 {expires}")
        if detail:
            print(f"      {detail}")
    if report["valid"] != report["total"]:
        print("\n写入路径仍被门禁阻断：四个门禁全部有效前，引擎不会提交任何选课请求。")
    return 0


def attest_issue(registry_path: Path, root: Path, *, gate: str, evidence: str,
                 reviewer: str, days: int, note: str) -> int:
    try:
        registry = AttestationRegistry.load(registry_path, root)
        attestation = AttestationRegistry.issue(
            root, gate=gate, evidence=evidence, reviewer=reviewer,
            expires_at=expiry_after(days) if days else None, note=note,
        )
        registry = registry.with_entry(attestation)
        registry.save(registry_path)
    except AttestationError as error:
        print(f"登记失败：{error}")
        return 2
    print(f"已登记 {gate}")
    print(f"  证据：{evidence}")
    print(f"  摘要：{attestation.contract_sha256}")
    print(f"  有效期至：{attestation.expires_at.isoformat() if attestation.expires_at else '长期'}")
    print("  提示：证据文件一旦改动，本登记自动失效，需要重新执行 issue。")
    return 0


def attest_revoke(registry_path: Path, root: Path, gate: str) -> int:
    try:
        registry = AttestationRegistry.load(registry_path, root)
        if gate not in registry.entries:
            print(f"{gate} 本来就没有登记，无需撤销。")
            return 0
        registry.without_entry(gate).save(registry_path)
    except AttestationError as error:
        print(f"撤销失败：{error}")
        return 2
    print(f"已撤销 {gate}；该门禁立即回到未登记状态。")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="attest", description="选课写入门禁的证据登记表管理（不访问学校）")
    parser.add_argument("--registry", help=f"登记表路径，默认 ./{REGISTRY_FILENAME}")
    parser.add_argument("--root", help="证据文件根目录，默认当前目录")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="显示四个门禁的证据状态")

    issue = sub.add_parser("issue", help="用一份证据文件登记一个门禁")
    issue.add_argument("--gate", required=True, choices=list(GATE_NAMES))
    issue.add_argument("--evidence", required=True, help="仓库内相对路径")
    issue.add_argument("--reviewer", required=True)
    issue.add_argument("--days", type=int, default=90,
                       help="有效期天数，0 表示不过期（默认 90）")
    issue.add_argument("--note", default="")

    revoke = sub.add_parser("revoke", help="撤销一个门禁")
    revoke.add_argument("--gate", required=True, choices=list(GATE_NAMES))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    registry_path, root = _default_registry(args.registry, args.root)
    if args.command == "status":
        return attest_status(registry_path, root)
    if args.command == "issue":
        return attest_issue(registry_path, root, gate=args.gate,
                            evidence=args.evidence, reviewer=args.reviewer,
                            days=args.days, note=args.note)
    return attest_revoke(registry_path, root, args.gate)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] or ["status"]))
