#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""仓库死代码审计器 —— 纯静态引用分析，给清理决策提供证据。

判据（三档，越靠下越保守）：

  ORPHAN     零入向引用，且不是入口点/测试/脚本
  SHIM       内容是对另一个同名模块的 re-export（薄壳）
  DUPLICATE  与另一路径同名且内容高度相似（疑似旧拷贝）

用法::

    python repo_audit/dead_code_audit.py --repo repo/kingo-assistant --out repo_audit/dead_code_audit.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

TEXT_SUFFIXES = {".py", ".md", ".yml", ".yaml", ".bat", ".txt", ".spec", ".json", ".toml", ".cfg", ".ini", ".ps1", ".sh"}
ENTRY_NAMES = {"__init__.py", "__main__.py", "main.py", "conftest.py", "setup.py"}
ENTRY_PREFIXES = ("test_", "run-", "build-", "debug-", "smoke_", "selftest")


def is_entry(rel: str) -> bool:
    name = Path(rel).name
    if name in ENTRY_NAMES:
        return True
    if name.startswith(ENTRY_PREFIXES):
        return True
    parts = Path(rel).parts
    if parts and parts[0] in ("tests",) or "tests" in parts:
        return True
    return False


@dataclass
class FileInfo:
    rel: str
    size: int
    sha: str
    lines: int
    imports: set[str] = field(default_factory=set)
    inbound: set[str] = field(default_factory=set)


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return ""


def module_names(rel: str) -> list[str]:
    """一个文件可能被引用的所有名字。"""
    path = Path(rel)
    stem = path.stem
    names = {stem}
    parts = list(path.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
        names.add(parts[-1] if parts else "")
    for start in range(len(parts)):
        names.add(".".join(parts[start:]))
    return [n for n in names if n]


def build_index(root: Path) -> dict[str, FileInfo]:
    index: dict[str, FileInfo] = {}
    for path in sorted(root.rglob("*.py")):
        rel = str(path.relative_to(root)).replace("\\", "/")
        if "/.git/" in "/" + rel or rel.startswith(".git/"):
            continue
        text = read_text(path)
        index[rel] = FileInfo(
            rel=rel,
            size=path.stat().st_size,
            sha=hashlib.sha256(path.read_bytes()).hexdigest(),
            lines=text.count("\n") + 1 if text else 0,
        )
        found = set()
        for match in re.finditer(r"^\s*from\s+([\w.]+)\s+import|^\s*import\s+([\w., ]+)", text, re.M):
            if match.group(1):
                found.add(match.group(1))
            if match.group(2):
                for piece in match.group(2).split(","):
                    found.add(piece.strip().split(" as ")[0].strip())
        index[rel].imports = {f for f in found if f}
    return index


def all_text_files(root: Path) -> list[Path]:
    out = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in (".git", "__pycache__", "build", "dist") for part in path.parts):
            continue
        if path.suffix.lower() in TEXT_SUFFIXES:
            out.append(path)
    return out


def classify(root: Path, index: dict[str, FileInfo]) -> dict:
    # 1) 计算入向引用：按模块名在整个仓库文本里搜
    texts: dict[str, str] = {}
    for path in all_text_files(root):
        rel = str(path.relative_to(root)).replace("\\", "/")
        texts[rel] = read_text(path)

    for rel, info in index.items():
        names = module_names(rel)
        for other_rel, text in texts.items():
            if other_rel == rel:
                continue
            for name in names:
                if not name:
                    continue
                if re.search(rf"(?<![\w.]){re.escape(name)}(?![\w])", text):
                    info.inbound.add(other_rel)
                    break

    # 2) 薄壳检测：正文去掉注释/空行/docstring 后 < 40 行且只做 import/__all__
    shims = []
    for rel, info in index.items():
        text = read_text(root / rel)
        body = [line for line in text.splitlines()
                if line.strip() and not line.strip().startswith("#")]
        code = [line for line in body
                if not re.match(r"^\s*(from|import)\b", line)
                and not re.match(r"^\s*__all__\b", line)
                and not line.strip().startswith(("\"\"\"", "'''"))]
        if len(body) <= 60 and len(code) <= 12 and "import" in text:
            shims.append({"rel": rel, "body_lines": len(body),
                          "non_import_lines": len(code)})

    # 3) 同名不同路径（疑似旧拷贝）
    by_stem: dict[str, list[str]] = {}
    for rel in index:
        by_stem.setdefault(Path(rel).stem, []).append(rel)
    duplicates = []
    for stem, rels in by_stem.items():
        if len(rels) < 2:
            continue
        for i in range(len(rels)):
            for j in range(i + 1, len(rels)):
                a, b = index[rels[i]], index[rels[j]]
                if a.size == 0 or b.size == 0:
                    continue
                ratio = min(a.size, b.size) / max(a.size, b.size)
                duplicates.append({"stem": stem, "a": a.rel, "b": b.rel,
                                   "size_a": a.size, "size_b": b.size,
                                   "size_ratio": round(ratio, 3),
                                   "same_sha": a.sha == b.sha})

    orphans = sorted(
        rel for rel, info in index.items()
        if not info.inbound and not is_entry(rel) and info.size > 0)
    empties = sorted(rel for rel, info in index.items() if info.size == 0)

    return {
        "root": str(root),
        "python_files": len(index),
        "orphans": [{"rel": r, "size": index[r].size, "lines": index[r].lines}
                    for r in orphans],
        "empty_files": empties,
        "shims": shims,
        "duplicates": duplicates,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="仓库死代码审计")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()
    root = Path(args.repo)
    if not root.is_dir():
        print(f"[x] 目录不存在：{root}")
        return 2
    index = build_index(root)
    report = classify(root, index)
    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
    print(f"Python 文件 {report['python_files']}")
    print(f"\n=== ORPHAN 零引用 ({len(report['orphans'])}) ===")
    for row in report["orphans"]:
        print(f"  {row['size']:>7}B {row['lines']:>5}行  {row['rel']}")
    print(f"\n=== 空文件 ({len(report['empty_files'])}) ===")
    for rel in report["empty_files"]:
        print(f"  {rel}")
    print(f"\n=== SHIM 薄壳 ({len(report['shims'])}) ===")
    for row in report["shims"]:
        print(f"  body={row['body_lines']:>3} code={row['non_import_lines']:>3}  {row['rel']}")
    print(f"\n=== DUPLICATE 同名 ({len(report['duplicates'])}) ===")
    for row in report["duplicates"]:
        mark = "IDENTICAL" if row["same_sha"] else f"ratio={row['size_ratio']}"
        print(f"  {mark:<12} {row['a']}\n               {row['b']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
