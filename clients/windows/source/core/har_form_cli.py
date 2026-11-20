"""Offline CLI: pair a captured HAR with its sanitized evidence file.

Two levels of proof, both local:

1. **Pairing** (always): does this HAR's write form hash to the ``items`` /
   ``hidOption`` / student id recorded in ``evidence/items_binding.json``? This
   is what catches "I edited the evidence" or "I captured a second attempt".
2. **Observation** (only with a full scope): additionally bind the form to a live
   session (account, semester, year, term, round) and produce the
   ``CurrentS202FormObservation`` that ``VerifiedS202FormProvider`` accepts.

Nothing here sends a request, prints a field value, or writes a file.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from core.course_models import CourseTarget
from core.ncwu_har_form_evidence import (
    FormEvidenceError, extract_submit_form, load_har, load_items_binding,
    prepare_from_files, verify_pairing,
)
from core.ncwu_live_elective import ORIGIN
from core.ncwu_verified_readback_bridge import ReadbackScope

_SCOPE_FIELDS = ("account_scope", "semester", "student_id",
                 "academic_year", "academic_term", "round")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="har-form",
        description="离线校验 HAR 写表单与 items_binding 证据是否配对（不联网、不提交）")
    parser.add_argument("--har", required=True, metavar="FILE",
                        help="包含一次成功 S202 POST 的 HAR")
    parser.add_argument("--evidence", required=True, metavar="FILE",
                        help="tools/patch/p0_protocol_recon.py 生成的 items_binding.json")
    parser.add_argument("--course", metavar="CODE",
                        help="目标课程代码；给出时才校验教学班绑定")
    parser.add_argument("--class-id", metavar="ID",
                        help="目标内部教学班 ID（skbjdm，不是页面 skbh）")
    parser.add_argument("--account-scope", metavar="S", help="已核验的本人账号范围")
    parser.add_argument("--semester", metavar="S", help="已核验的选课学期")
    parser.add_argument("--student-id", metavar="S", help="当前会话学号（仅在本进程内使用）")
    parser.add_argument("--academic-year", metavar="S", help="当前学年")
    parser.add_argument("--academic-term", metavar="S", help="当前学期")
    parser.add_argument("--round", metavar="S", help="当前选课轮次 ID（lcid）")
    parser.add_argument("--json", action="store_true", help="只输出 JSON")
    return parser


def _scope_values(args) -> dict:
    return {name: getattr(args, name) for name in _SCOPE_FIELDS}


def _missing_scope(values: dict) -> list[str]:
    return [name for name in _SCOPE_FIELDS
            if not (isinstance(values.get(name), str) and values[name].strip())]


def run(argv: list[str]) -> int:
    args = _build_parser().parse_args(argv)
    result: dict = {"ok": False, "stage": "load", "errors": []}
    try:
        binding = load_items_binding(Path(args.evidence))
        form = extract_submit_form(load_har(Path(args.har)))
    except FormEvidenceError as exc:
        result["errors"].append(str(exc))
        return _emit(result, args.json, 2)

    result["stage"] = "pairing"
    result["evidence"] = binding.as_dict()
    result["har"] = {
        "http_status": form.status,
        "origin": form.origin,
        "fields": [name for name, _ in form.fields],
        "captured_at": form.captured_at,
    }
    try:
        result["cross_check"] = verify_pairing(form, binding)
    except FormEvidenceError as exc:
        result["errors"].append(str(exc))
        return _emit(result, args.json, 3)

    target = None
    if args.course or args.class_id:
        if not (args.course and args.class_id):
            result["errors"].append("校验教学班绑定需要同时给出 --course 与 --class-id")
            return _emit(result, args.json, 2)
        try:
            target = CourseTarget(key="cli", course_id=args.course,
                                  class_id=args.class_id)
        except ValueError as exc:
            result["errors"].append(f"目标课程参数无效：{exc}")
            return _emit(result, args.json, 2)
        if (binding.course_id != target.course_id
                or binding.class_id != target.class_id):
            result["errors"].append(
                "证据绑定的课程/教学班与 --course/--class-id 不一致")
            return _emit(result, args.json, 3)

    scope_values = _scope_values(args)
    missing = _missing_scope(scope_values)
    if target is not None and not missing:
        try:
            scope = ReadbackScope(
                account_scope=scope_values["account_scope"],
                semester=scope_values["semester"],
                school_origin=ORIGIN,
                internal_student_id=scope_values["student_id"],
                academic_year=scope_values["academic_year"],
                academic_term=scope_values["academic_term"],
                round_id=scope_values["round"],
                session_proof="har-form-cli",
            )
            prepare_from_files(
                har_path=Path(args.har), binding_path=Path(args.evidence),
                scope=scope, target=target)
        except (FormEvidenceError, ValueError) as exc:
            result["errors"].append(str(exc))
            return _emit(result, args.json, 4)
        result.update(ok=True, stage="observation", observation="ready",
                      note="已构造 provider 可接受的 observation；本命令未提交任何请求")
        return _emit(result, args.json, 0)

    result.update(ok=True, stage="pairing", missing_scope=missing,
                  note="配对校验通过；补齐 scope 与 --course/--class-id 可验证可提交性")
    return _emit(result, args.json, 0)


def _emit(result: dict, as_json: bool, code: int) -> int:
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return code
    print("== HAR 写表单 × 映射证据 离线校验 ==")
    if result.get("har"):
        print(f"  来源 / 状态码  : {result['har']['origin']} / {result['har']['http_status']}")
        print(f"  表单字段       : {', '.join(result['har']['fields'])}")
        if result["har"]["captured_at"]:
            print(f"  抓包时间       : {result['har']['captured_at']}")
    if result.get("evidence"):
        evidence = result["evidence"]
        print(f"  课程 / 教学班  : {evidence['course_id']} / {evidence['class_id']}")
        print(f"  绑定字段       : {evidence['matched_field']}"
              f"（强度 {evidence['match_strength']}）")
        print(f"  items 长度     : {evidence['items_length']}")
        print(f"  items sha256   : {evidence['items_sha256']}")
    if result.get("cross_check") and result.get("ok"):
        print(f"  哈希配对       : 通过（证据 sha256 {result['cross_check']['evidence_sha256']}）")
    for message in result.get("errors", []):
        print(f"  [x] {message}")
    if result.get("missing_scope"):
        print(f"  未提供 scope   : {', '.join(result['missing_scope'])}")
    if result.get("note"):
        print(f"  结论           : {'通过' if result['ok'] else '不通过'} — {result['note']}")
    return code


def main(argv: list[str] | None = None) -> int:
    try:
        return run(list(sys.argv[1:] if argv is None else argv))
    except SystemExit as exc:
        return int(exc.code or 0)
