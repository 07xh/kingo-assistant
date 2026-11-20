#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""V1/V2 限速解锁补丁 —— 解除 max_concurrency=1 硬约束、拆掉 30s 硬下限。

改的是本地 clone 的 ``clients/windows/source/core/`` 下两个文件：

    request_budget.py
      * RequestPolicy.max_concurrency 允许 1–16
      * max_concurrency > 1 时跳过跨进程排他租约（滑动窗口仍生效）

    selection_optim.py
      * ABSOLUTE_MIN_INTERVAL 改为读 KINGO_MIN_INTERVAL_SECONDS 环境变量，
        默认仍是 30，不设环境变量时行为与官方一致

全部改动先备份到 ``<repo>/.kingo_patch_backup/<时间戳>/``，``revert`` 可完整还原。

用法::

    python v1v2_limit_lift.py status  --repo D:\\code\\kingo-assistant
    python v1v2_limit_lift.py apply   --repo D:\\code\\kingo-assistant
    python v1v2_limit_lift.py apply   --repo ... --concurrency 4 --floor 2
    python v1v2_limit_lift.py revert  --repo D:\\code\\kingo-assistant
    python v1v2_limit_lift.py selftest

注意（实测数据，不是意见）：仓库自带 bench 显示 8 并发把提交成功率从
1.00 打到 0.125、p95 从 70ms 升到 1178ms。并发只对"多目标教学班并行探测"
有意义，对"同一课程重复提交"是负收益。
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

BACKUP_DIRNAME = ".kingo_patch_backup"
MANIFEST = "manifest.json"


@dataclass(frozen=True)
class Edit:
    name: str
    relative: str
    old: str
    new: str


def build_edits(concurrency: int, floor: float) -> list[Edit]:
    budget = "clients/windows/source/core/request_budget.py"
    select = "clients/windows/source/core/selection_optim.py"
    return [
        Edit(
            name="v1_policy_range",
            relative=budget,
            old=(
                "        if type(self.max_concurrency) is not int or self.max_concurrency != 1:\n"
                '            raise PolicyError("当前跨进程串行实现只支持 max_concurrency=1")'
            ),
            new=(
                f"        if type(self.max_concurrency) is not int or not 1 <= self.max_concurrency <= {concurrency}:\n"
                f'            raise PolicyError("max_concurrency 必须在 1–{concurrency} 之间")'
            ),
        ),
        Edit(
            name="v1_lease_gate",
            relative=budget,
            old=(
                "        async with self._concurrency:\n"
                "            lease = await self._process_lease()\n"
                "            try:\n"
                "                await self._reserve()\n"
                "                yield\n"
                "            finally:\n"
                "                self._release_process_lease(lease)"
            ),
            new=(
                "        async with self._concurrency:\n"
                "            # 单并发保留跨进程排他租约；多并发时由滑动窗口独立兜底。\n"
                "            lease = (await self._process_lease()\n"
                "                     if self.policy.max_concurrency == 1 else None)\n"
                "            try:\n"
                "                await self._reserve()\n"
                "                yield\n"
                "            finally:\n"
                "                self._release_process_lease(lease)"
            ),
        ),
        Edit(
            name="v1_lease_none",
            relative=budget,
            old=(
                "    @staticmethod\n"
                "    def _release_process_lease(lease) -> None:\n"
                "        stream, local = lease"
            ),
            new=(
                "    @staticmethod\n"
                "    def _release_process_lease(lease) -> None:\n"
                "        if lease is None:\n"
                "            return\n"
                "        stream, local = lease"
            ),
        ),
        Edit(
            name="v2_add_os_import",
            relative=select,
            old="import datetime as _dt\nimport json\nimport statistics",
            new="import datetime as _dt\nimport json\nimport os\nimport statistics",
        ),
        Edit(
            name="v2_env_floor",
            relative=select,
            old="ABSOLUTE_MIN_INTERVAL = 30.0",
            new=(
                'ABSOLUTE_MIN_INTERVAL = float(os.environ.get(\n'
                '    "KINGO_MIN_INTERVAL_SECONDS", "30"))'
            ),
        ),
    ]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".kingo.tmp")
    tmp.write_text(text, encoding="utf-8", newline="")
    tmp.replace(path)


def classify(root: Path, edit: Edit) -> str:
    path = root / edit.relative
    if not path.is_file():
        return "missing_file"
    text = read(path)
    has_old = edit.old in text
    has_new = edit.new in text
    if has_old and not has_new:
        return "pending"
    if has_new and not has_old:
        return "applied"
    if has_new and has_old:
        return "ambiguous"
    return "not_found"


def backup(root: Path, stamp: str, relatives: list[str]) -> dict[str, str]:
    base = root / BACKUP_DIRNAME / stamp
    saved: dict[str, str] = {}
    for relative in relatives:
        source = root / relative
        target = base / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        saved[relative] = str(target)
    return saved


def write_manifest(root: Path, stamp: str, payload: dict) -> Path:
    path = root / BACKUP_DIRNAME / stamp / MANIFEST
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def latest_stamp(root: Path) -> str | None:
    base = root / BACKUP_DIRNAME
    if not base.is_dir():
        return None
    stamps = sorted(p.name for p in base.iterdir()
                    if p.is_dir() and (p / MANIFEST).is_file())
    return stamps[-1] if stamps else None


def compile_check(files: list[Path]) -> tuple[bool, str]:
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", *[str(f) for f in files]],
        capture_output=True, text=True)
    output = (result.stdout or "") + (result.stderr or "")
    return result.returncode == 0, output.strip()


def cmd_status(args) -> int:
    root = Path(args.repo)
    if not root.is_dir():
        print(f"[x] 仓库目录不存在：{root}")
        return 2
    edits = build_edits(args.concurrency, args.floor)
    print(f"repo = {root}")
    for edit in edits:
        print(f"  {edit.name:<20} {edit.relative:<48} {classify(root, edit)}")
    stamp = latest_stamp(root)
    print(f"\n最近备份 = {stamp or '无'}")

    # 提示其它仍带 30s 常量的文件（不猜内容，只报位置）
    suspects: list[str] = []
    for path in (root / "clients").rglob("*.py"):
        try:
            text = read(path)
        except (UnicodeDecodeError, OSError):
            continue
        if "ABSOLUTE_MIN_INTERVAL" in text or "30.0" in text and "interval" in text.lower():
            relative = str(path.relative_to(root)).replace("\\", "/")
            if relative not in {e.relative for e in edits}:
                suspects.append(relative)
    if suspects:
        print("\n[!] 以下文件也含 30s 相关常量，需人工确认同源下限：")
        for relative in sorted(set(suspects))[:20]:
            print(f"    {relative}")
    return 0


def cmd_apply(args) -> int:
    root = Path(args.repo)
    if not root.is_dir():
        print(f"[x] 仓库目录不存在：{root}")
        return 2
    edits = build_edits(args.concurrency, args.floor)
    states = {edit.name: classify(root, edit) for edit in edits}
    pending = [e for e in edits if states[e.name] == "pending"]
    blocked = [e for e in edits if states[e.name] in ("missing_file", "not_found", "ambiguous")]
    if blocked:
        print("[x] 以下改动无法定位（源码版本可能已变），整体放弃，未修改任何文件：")
        for edit in blocked:
            print(f"    {edit.name:<20} {edit.relative}  state={states[edit.name]}")
        return 3
    if not pending:
        print("[=] 全部改动已是目标状态，无需操作")
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    relatives = sorted({e.relative for e in pending})
    saved = backup(root, stamp, relatives)
    print(f"[+] 已备份 {len(relatives)} 个文件 -> {root / BACKUP_DIRNAME / stamp}")

    applied: list[str] = []
    try:
        for edit in pending:
            path = root / edit.relative
            text = read(path)
            if edit.old not in text:
                raise RuntimeError(f"{edit.name} 目标文本消失，中止")
            write(path, text.replace(edit.old, edit.new, 1))
            applied.append(edit.name)
            print(f"    patched {edit.name}")
    except BaseException as exc:
        print(f"[x] 失败：{exc}；开始回滚")
        for relative, source in saved.items():
            shutil.copy2(source, root / relative)
        print("[=] 已回滚到打补丁前状态")
        return 4

    ok, output = compile_check([root / r for r in relatives])
    manifest = {
        "stamp": stamp,
        "applied_at": datetime.now().isoformat(timespec="seconds"),
        "concurrency": args.concurrency,
        "floor": args.floor,
        "edits": applied,
        "backups": saved,
        "compile_ok": ok,
        "compile_output": output,
    }
    path = write_manifest(root, stamp, manifest)
    print(f"[+] manifest -> {path}")
    if not ok:
        print("[x] py_compile 失败，输出如下，用 revert 还原：")
        print(output)
        return 5
    print("[+] py_compile OK")
    print("\n生效方式：")
    print(f"  1) config.yaml 里 server_policy.max_concurrency 设为 2–{args.concurrency}")
    print(f"  2) 需要低于 30s 轮询时设环境变量 "
          f"KINGO_MIN_INTERVAL_SECONDS={args.floor:g}")
    print("  3) 不给环境变量则行为与官方完全一致（30s）")
    return 0


def cmd_revert(args) -> int:
    root = Path(args.repo)
    stamp = args.stamp or latest_stamp(root)
    if not stamp:
        print("[x] 没有可用备份")
        return 2
    manifest_path = root / BACKUP_DIRNAME / stamp / MANIFEST
    if not manifest_path.is_file():
        print(f"[x] manifest 不存在：{manifest_path}")
        return 3
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    restored = []
    for relative, source in manifest["backups"].items():
        source_path = Path(source)
        if not source_path.is_file():
            print(f"[x] 备份缺失：{source_path}")
            return 4
        shutil.copy2(source_path, root / relative)
        restored.append(relative)
    ok, output = compile_check([root / r for r in restored])
    print(f"[+] 已还原 {len(restored)} 个文件（备份 {stamp}）")
    print("    py_compile", "OK" if ok else "FAILED")
    if not ok:
        print(output)
        return 5
    return 0


def cmd_selftest(args) -> int:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        budget_fake = (
            "class RequestPolicy:\n"
            "    def __post_init__(self) -> None:\n"
            "        if type(self.max_concurrency) is not int or self.max_concurrency != 1:\n"
            '            raise PolicyError("当前跨进程串行实现只支持 max_concurrency=1")\n'
            "\n"
            "    async def request(self):\n"
            "        async with self._concurrency:\n"
            "            lease = await self._process_lease()\n"
            "            try:\n"
            "                await self._reserve()\n"
            "                yield\n"
            "            finally:\n"
            "                self._release_process_lease(lease)\n"
            "\n"
            "    @staticmethod\n"
            "    def _release_process_lease(lease) -> None:\n"
            "        stream, local = lease\n"
        )
        select_fake = (
            "import datetime as _dt\n"
            "import json\n"
            "import statistics\n"
            "import time\n"
            "\n"
            "ABSOLUTE_MIN_INTERVAL = 30.0\n"
        )
        for relative, text in (("clients/windows/source/core/request_budget.py", budget_fake),
                               ("clients/windows/source/core/selection_optim.py", select_fake)):
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8", newline="")

        edits = build_edits(4, 2.0)
        for edit in edits:
            state = classify(root, edit)
            assert state == "pending", f"{edit.name} 期望 pending，实际 {state}"

        args2 = argparse.Namespace(repo=str(root), concurrency=4, floor=2.0, stamp=None)
        code = cmd_apply(args2)
        assert code == 0, f"apply 返回 {code}"

        budget = (root / "clients/windows/source/core/request_budget.py").read_text(encoding="utf-8")
        assert "not 1 <= self.max_concurrency <= 4" in budget
        assert "if lease is None:" in budget
        assert "if self.policy.max_concurrency == 1 else None" in budget
        select = (root / "clients/windows/source/core/selection_optim.py").read_text(encoding="utf-8")
        assert "import os" in select
        assert "KINGO_MIN_INTERVAL_SECONDS" in select

        for edit in edits:
            state = classify(root, edit)
            assert state == "applied", f"{edit.name} 期望 applied，实际 {state}"

        code = cmd_revert(args2)
        assert code == 0, f"revert 返回 {code}"
        assert "ABSOLUTE_MIN_INTERVAL = 30.0" in (
            root / "clients/windows/source/core/selection_optim.py").read_text(encoding="utf-8")
        assert "max_concurrency != 1" in (
            root / "clients/windows/source/core/request_budget.py").read_text(encoding="utf-8")

    print("PASS selftest")
    print("  apply: 5 处改动定位成功、备份齐全、py_compile 通过")
    print("  revert: 逐字节还原原始内容")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="V1/V2 限速解锁补丁")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--repo", required=True, help="kingo-assistant 本地 clone 根目录")
        p.add_argument("--concurrency", type=int, default=4,
                       help="max_concurrency 上界（默认 4）")
        p.add_argument("--floor", type=float, default=2.0,
                       help="30s 下限可下调到的值（默认 2s）")

    status = sub.add_parser("status")
    common(status)
    status.set_defaults(func=cmd_status)

    apply_p = sub.add_parser("apply")
    common(apply_p)
    apply_p.set_defaults(func=cmd_apply)

    revert = sub.add_parser("revert")
    revert.add_argument("--repo", required=True)
    revert.add_argument("--stamp", default=None)
    revert.set_defaults(func=cmd_revert)

    selftest = sub.add_parser("selftest")
    selftest.set_defaults(func=cmd_selftest)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
