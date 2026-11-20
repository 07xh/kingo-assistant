#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""P0 写协议还原器 —— 从真实抓包还原 S202 选课写入合同。

目标：把 ``/hsjw/STU_ElectCourseAction.do`` 的 7 字段表单、items 编码、
lcid 与内部教学班 ID 的映射，从一次真实成功请求里确定性地提取出来，
产出三件 attestation 可直接消费的产物：

    evidence/items_binding.json          原始映射证据（字节即哈希对象）
    evidence/official_write_contract.md  人工可复核的写入合同（注册为 attestation 证据）
    evidence/attest_issue_official_write.py  一条命令完成 official_write_verified 登记

用法::

    # 1) 抓一次真实成功正选（浏览器 DevTools -> 保存全部为 HAR）
    python p0_protocol_recon.py scan --har capture.har --catalog catalog.json

    # 2) 复核证据文件哈希（喂给 PreparedNCWUSubmission.item_binding_sha256）
    python p0_protocol_recon.py verify --binding evidence/items_binding.json

    # 3) 自检（内置合成抓包，不联网）
    python p0_protocol_recon.py selftest

设计约束（与仓库 core/ncwu_submission_transport.py 完全一致）：
* 只接受 ``xktype=2``（S202 正选）
* ``items`` 不得含 ``|``（禁止一次提交多课程），长度 <= 512
* 7 个必需字段：xh / xn / xq / lcid / xktype / hidOption / items
* 没有 catalog 映射证据时不签发 attestation 代码，只落数据
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

SUBMIT_PATH = "/hsjw/STU_ElectCourseAction.do"
REQUIRED_FIELDS = ("xh", "xn", "xq", "lcid", "xktype", "hidOption", "items")
MAX_ITEMS_LEN = 512
GATE = "official_write_verified"


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------

def sha256_bytes(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_json(payload: Any) -> bytes:
    """确定性的证据字节：排序键 + 紧凑分隔符 + UTF-8。"""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def mask(value: str, keep: int = 4) -> str:
    text = value or ""
    if len(text) <= keep:
        return "*" * len(text)
    return "*" * (len(text) - keep) + text[-keep:]


def form_pairs(raw_text: str) -> list[tuple[str, str]]:
    """把 HAR postData.text 还原成有序键值对。

    Kingosoft 表单是 GBK 百分号编码。HAR 里可能是原始字符串，也可能是
    已解码字符串，两种都要能吃。取字段数最多且无替换字符的一版。
    """
    candidates: list[list[tuple[str, str]]] = []
    try:
        blob = urllib.parse.unquote_to_bytes(raw_text)
    except (UnicodeError, ValueError):
        blob = b""
    if blob:
        for encoding in ("gbk", "gb18030", "utf-8"):
            try:
                decoded = blob.decode(encoding)
            except UnicodeDecodeError:
                continue
            candidates.append(urllib.parse.parse_qsl(decoded, keep_blank_values=True))
    candidates.append(urllib.parse.parse_qsl(raw_text, keep_blank_values=True))

    def score(pairs: list[tuple[str, str]]) -> tuple[int, int]:
        good = sum(1 for _, value in pairs if "\ufffd" not in value)
        return len(pairs), good

    return max(candidates, key=score) if candidates else []


def load_har(path: Path) -> dict:
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        document = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if not isinstance(document, dict) or "log" not in document:
        raise SystemExit(f"[x] 不是合法 HAR：{path}")
    entries = (document.get("log") or {}).get("entries")
    if not isinstance(entries, list):
        raise SystemExit(f"[x] HAR 缺少 log.entries：{path}")
    return document


def header_map(headers: Iterable[dict]) -> dict[str, str]:
    result: dict[str, str] = {}
    for item in headers or []:
        name = str(item.get("name", "")).lower()
        if name:
            result[name] = str(item.get("value", ""))
    return result


# ---------------------------------------------------------------------------
# 抓包 -> 提交样本
# ---------------------------------------------------------------------------

@dataclass
class SubmitSample:
    started_at: str
    url: str
    origin: str
    status: int
    fields: list[tuple[str, str]]
    referer: str
    content_type: str

    @property
    def mapping(self) -> dict[str, str]:
        return {key: value for key, value in self.fields}

    def field(self, name: str) -> str:
        return self.mapping.get(name, "")


def collect_samples(har: dict) -> list[SubmitSample]:
    samples: list[SubmitSample] = []
    for entry in har["log"]["entries"]:
        request = entry.get("request") or {}
        raw_url = str(request.get("url", ""))
        if SUBMIT_PATH not in raw_url:
            continue
        if str(request.get("method", "")).upper() != "POST":
            continue
        post = request.get("postData") or {}
        text = post.get("text")
        if isinstance(text, dict):
            text = "&".join(f"{k}={v}" for k, v in text.items())
        if not isinstance(text, str) or not text:
            continue
        pairs = form_pairs(text)
        if not pairs:
            continue
        parsed = urllib.parse.urlsplit(raw_url)
        samples.append(SubmitSample(
            started_at=str(entry.get("startedDateTime", "")),
            url=raw_url,
            origin=f"{parsed.scheme}://{parsed.netloc}",
            status=int((entry.get("response") or {}).get("status") or 0),
            fields=pairs,
            referer=header_map(request.get("headers") or []).get("referer", ""),
            content_type=header_map(request.get("headers") or []).get(
                "content-type", post.get("mimeType", "")),
        ))
    return samples


def validate_sample(sample: SubmitSample) -> tuple[bool, list[str]]:
    problems: list[str] = []
    mapping = sample.mapping
    missing = [name for name in REQUIRED_FIELDS if not mapping.get(name)]
    if missing:
        problems.append("缺少字段: " + ",".join(missing))
    if mapping.get("xktype") and mapping["xktype"] != "2":
        problems.append(f"xktype={mapping['xktype']}，不是 S202 正选")
    items = mapping.get("items", "")
    if "|" in items:
        problems.append("items 含多课程分隔符 '|'")
    if len(items) > MAX_ITEMS_LEN:
        problems.append(f"items 长度 {len(items)} 超过 {MAX_ITEMS_LEN}")
    if sample.status not in (200,):
        problems.append(f"HTTP {sample.status}，不是可确认的成功样本")
    return (not problems), problems


# ---------------------------------------------------------------------------
# items -> 内部教学班 ID 绑定
# ---------------------------------------------------------------------------

def load_catalog(path: Path | None) -> list[dict]:
    if path is None:
        return []
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(document, dict):
        for key in ("offerings", "list", "items", "courses"):
            value = document.get(key)
            if isinstance(value, list):
                document = value
                break
    if not isinstance(document, list):
        raise SystemExit(f"[x] catalog 需为数组或含 offerings 数组：{path}")
    return [row for row in document if isinstance(row, dict)]


def bind_teaching_class(items: str, catalog: list[dict]) -> dict | None:
    """在 catalog 中找到 items 字符串里实际承载的内部教学班 ID。

    优先匹配 ``skbjdm``（内部 ID，与页面 skbh 不等价），其次 ``class_id``，
    再其次 ``skbh``（仅作弱证据，标注 low）。
    """
    for field, strength in (("skbjdm", "high"), ("class_id", "high"), ("skbh", "low")):
        for row in catalog:
            token = str(row.get(field) or "").strip()
            if token and token in items:
                return {
                    "course_id": str(row.get("course_id") or row.get("kcdm") or ""),
                    "class_id": str(row.get("class_id") or row.get("skbjdm") or ""),
                    "skbh": str(row.get("skbh") or ""),
                    "skbjdm": str(row.get("skbjdm") or ""),
                    "matched_field": field,
                    "match_strength": strength,
                    "matched_token": token,
                    "remain": row.get("remain"),
                    "name": str(row.get("name") or row.get("kcmc") or ""),
                }
    return None


# ---------------------------------------------------------------------------
# 证据生成
# ---------------------------------------------------------------------------

def build_evidence(sample: SubmitSample, har_sha: str, catalog: list[dict],
                   catalog_sha: str | None) -> dict:
    items = sample.field("items")
    binding = bind_teaching_class(items, catalog) if catalog else None
    return {
        "version": 1,
        "gate": GATE,
        "captured_at": sample.started_at or datetime.now(timezone.utc).isoformat(),
        "source": {
            "har_sha256": har_sha,
            "catalog_sha256": catalog_sha,
            "origin": sample.origin,
            "path": SUBMIT_PATH,
            "referer_present": bool(sample.referer),
            "content_type": sample.content_type,
            "http_status": sample.status,
        },
        "form": {
            "field_order": [name for name, _ in sample.fields],
            "required_present": sorted(
                name for name in REQUIRED_FIELDS if sample.field(name)),
            "xktype": sample.field("xktype"),
            "academic_year": sample.field("xn"),
            "academic_term": sample.field("xq"),
            "round_id": sample.field("lcid"),
            "hid_option_len": len(sample.field("hidOption")),
            "hid_option_sha256": sha256_bytes(
                sample.field("hidOption").encode("utf-8")),
            "student_id_sha256": sha256_bytes(
                sample.field("xh").encode("utf-8")),
            "student_id_tail": mask(sample.field("xh"), 4),
        },
        "items": {
            "value_sha256": sha256_bytes(items.encode("utf-8")),
            "length": len(items),
            "contains_pipe": "|" in items,
            "shape": _shape(items),
        },
        "bound_teaching_class": binding,
        "mapping_proven": binding is not None and binding["match_strength"] == "high",
    }


def _shape(items: str) -> str:
    """把 items 归成 shape 模板，便于跨样本比对而不用暴露原值。"""
    out = []
    for ch in items:
        out.append("d" if ch.isdigit() else ("a" if ch.isalpha() else ch))
    collapsed: list[str] = []
    for ch in out:
        if not collapsed or collapsed[-1] != ch:
            collapsed.append(ch)
    return "".join(collapsed)[:80]


def render_contract(evidence: dict, evidence_sha: str) -> str:
    binding = evidence["bound_teaching_class"]
    form = evidence["form"]
    items = evidence["items"]
    source = evidence["source"]
    rows = [
        ("xh", "学号（本人会话）", "内部学号，非登录手机号"),
        ("xn", "学年", f"本次 = {form['academic_year']}"),
        ("xq", "学期", f"本次 = {form['academic_term']}"),
        ("lcid", "选课轮次 ID", f"本次 = {form['round_id']}"),
        ("xktype", "选课类型", "必须为 2（S202 正选）"),
        ("hidOption", "会话动态令牌", f"长度 {form['hid_option_len']}，sha256 前缀 "
                                    f"{form['hid_option_sha256'][:16]}"),
        ("items", "选课项编码", f"长度 {items['length']}，shape=`{items['shape']}`，"
                               f"含 '|' = {items['contains_pipe']}"),
    ]
    table = "\n".join(f"| `{name}` | {meaning} | {note} |" for name, meaning, note in rows)
    if binding:
        binding_block = (
            f"| 项 | 值 |\n|---|---|\n"
            f"| course_id | `{binding['course_id']}` |\n"
            f"| class_id | `{binding['class_id']}` |\n"
            f"| skbjdm | `{binding['skbjdm']}` |\n"
            f"| skbh | `{binding['skbh']}` |\n"
            f"| 匹配字段 | `{binding['matched_field']}`（强度 {binding['match_strength']}）|\n"
            f"| 匹配 token | `{binding['matched_token']}` |\n"
        )
    else:
        binding_block = "**未绑定**：本次 scan 没有提供可命中的 catalog，映射证据不足，不签发 attestation。\n"
    return f"""# 官方写入选课合同（official_write_verified）

由 `p0_protocol_recon.py` 从真实抓包还原，gate = `{GATE}`。

## 一、来源

| 项 | 值 |
|---|---|
| 抓包时间 | {evidence['captured_at']} |
| Origin | `{source['origin']}` |
| 路径 | `{source['path']}` |
| HAR sha256 | `{source['har_sha256']}` |
| catalog sha256 | `{source['catalog_sha256'] or '未提供'}` |
| HTTP 状态 | {source['http_status']} |
| Content-Type | `{source['content_type']}` |
| Referer 存在 | {source['referer_present']} |
| 证据文件 sha256 | `{evidence_sha}` |

## 二、表单七字段

| 字段 | 含义 | 本次核对 |
|---|---|---|
{table}

字段顺序与抓包一致：`{ " , ".join(form['field_order']) }`

## 三、items 与内部教学班绑定

{binding_block}
## 四、编码规则（照此构造，禁止推断）

1. 表单为 `application/x-www-form-urlencoded; charset=GBK`，字段按上表顺序排列。
2. `items` 只承载**单个**教学班编码；出现 `|` 即为多课程提交，直接拒绝。
3. `items` 长度上限 {MAX_ITEMS_LEN}；`hidOption` 与会话绑定，跨会话不可复用。
4. `xktype` 必须为 `2`；非 2 一律不发送。
5. 提交后不得重试，只以官方 `course+class` 回读判定成功。

## 五、登记命令

```powershell
python evidence/attest_issue_official_write.py
```

原始映射证据（字节即哈希对象）：
`evidence/items_binding.json`
sha256 = `{evidence_sha}`
"""


ATTEST_TEMPLATE = '''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""登记 official_write_verified 验收记录（由 p0_protocol_recon.py 生成）。"""
from pathlib import Path
import sys

REPO = Path(r"{repo}")
sys.path.insert(0, str(REPO / "clients" / "windows" / "source"))

from core.attestation import AttestationRegistry, expiry_after  # noqa: E402


def main() -> int:
    registry_path = REPO / "attestations.json"
    registry = AttestationRegistry.load(registry_path, REPO)
    entry = AttestationRegistry.issue(
        REPO,
        gate="official_write_verified",
        evidence=r"{evidence}",
        reviewer="{reviewer}",
        expires_at=expiry_after({days}),
        note="S202 items binding, capture sha256={sha}",
    )
    registry.with_entry(entry).save(registry_path)
    report = registry.describe()
    print("valid %d/%d" % (report["valid"], report["total"]))
    for row in report["rows"]:
        print("  %-32s %-8s %s" % (row["gate"], row["state"], row["evidence"]))
    return 0 if registry.all_valid() else 1


if __name__ == "__main__":
    raise SystemExit(main())
'''


def emit_attest_script(path: Path, repo: Path, evidence_rel: str,
                       reviewer: str, days: int, sha: str) -> None:
    path.write_text(ATTEST_TEMPLATE.format(
        repo=str(repo), evidence=evidence_rel.replace("\\", "/"),
        reviewer=reviewer, days=days, sha=sha), encoding="utf-8")


def write_outputs(sample: SubmitSample, har_sha: str, har_path: Path,
                  catalog: list[dict], catalog_path: Path | None,
                  out_dir: Path, repo: Path, reviewer: str, days: int) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    catalog_sha = sha256_file(catalog_path) if catalog_path else None
    evidence = build_evidence(sample, har_sha, catalog, catalog_sha)
    evidence_bytes = canonical_json(evidence)
    binding_path = out_dir / "items_binding.json"
    binding_path.write_bytes(evidence_bytes)
    evidence_sha = sha256_bytes(evidence_bytes)

    contract_path = out_dir / "official_write_contract.md"
    contract_path.write_text(render_contract(evidence, evidence_sha), encoding="utf-8")

    result = {
        "binding": str(binding_path),
        "binding_sha256": evidence_sha,
        "contract": str(contract_path),
        "mapping_proven": evidence["mapping_proven"],
        "attest_script": None,
    }
    if evidence["mapping_proven"]:
        script_path = out_dir / "attest_issue_official_write.py"
        emit_attest_script(script_path, repo, str(contract_path.resolve().relative_to(
            repo.resolve())) if _is_inside(contract_path, repo) else str(contract_path),
            reviewer, days, evidence_sha)
        result["attest_script"] = str(script_path)
    return result


def _is_inside(path: Path, root: Path) -> bool:
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# 命令
# ---------------------------------------------------------------------------

def cmd_scan(args) -> int:
    har_path = Path(args.har)
    if not har_path.is_file():
        print(f"[x] HAR 不存在：{har_path}")
        return 2
    har = load_har(har_path)
    samples = collect_samples(har)
    if not samples:
        print(f"[x] HAR 里没有 {SUBMIT_PATH} 的 POST。"
              f"确认抓包时勾选了保留请求体，且这次是真实提交。")
        return 3

    print(f"[+] 找到 {len(samples)} 条提交请求")
    good = []
    for index, sample in enumerate(samples, 1):
        ok, problems = validate_sample(sample)
        print(f"  [{index}] {sample.started_at} status={sample.status} "
              f"fields={len(sample.fields)} -> {'OK' if ok else 'REJECT'}")
        for problem in problems:
            print(f"        - {problem}")
        if ok:
            good.append(sample)
    if not good:
        print("[x] 没有通过校验的样本，无法签发证据")
        return 4

    sample = good[0]
    if len(good) > 1:
        print(f"[!] 有 {len(good)} 条合格样本，取第一条（{sample.started_at}）")

    catalog = load_catalog(Path(args.catalog) if args.catalog else None)
    if args.catalog and not catalog:
        print(f"[!] catalog 为空：{args.catalog}")
    result = write_outputs(
        sample, sha256_file(har_path), har_path, catalog,
        Path(args.catalog) if args.catalog else None,
        Path(args.out), Path(args.repo), args.reviewer, args.days)

    mapping = sample.mapping
    print("\n=== 表单字段（脱敏预览）===")
    for name in REQUIRED_FIELDS:
        value = mapping.get(name, "")
        shown = (mask(value, 4) if name in ("xh", "items", "hidOption") else value)
        print(f"  {name:<10} = {shown}")
    print(f"\n[+] items_binding.json  sha256 = {result['binding_sha256']}")
    print(f"[+] 写入合同            {result['contract']}")
    if result["attest_script"]:
        print(f"[+] attestation 脚本    {result['attest_script']}")
        print("    运行它即可把 official_write_verified 置为 valid")
    else:
        print("[!] 未证明 items -> 内部教学班映射，attestation 脚本未生成")
        print("    补 --catalog（含 skbjdm/class_id 的选课目录导出）后重跑")
    if args.print_form:
        print("\n=== 原始表单（含个人信息，勿落盘）===")
        for name, value in sample.fields:
            print(f"  {name}={value}")
    return 0


def cmd_verify(args) -> int:
    path = Path(args.binding)
    if not path.is_file():
        print(f"[x] 证据不存在：{path}")
        return 2
    digest = sha256_file(path)
    print(f"sha256 = {digest}")
    if args.sha:
        ok = digest.lower() == args.sha.lower()
        print("MATCH" if ok else "MISMATCH")
        return 0 if ok else 5
    payload = json.loads(path.read_text(encoding="utf-8"))
    print(f"gate          = {payload.get('gate')}")
    print(f"xktype        = {(payload.get('form') or {}).get('xktype')}")
    print(f"round_id      = {(payload.get('form') or {}).get('round_id')}")
    print(f"items.shape   = {(payload.get('items') or {}).get('shape')}")
    print(f"mapping_proven= {payload.get('mapping_proven')}")
    binding = payload.get("bound_teaching_class") or {}
    if binding:
        print(f"bound         = {binding.get('course_id')} / {binding.get('class_id')}"
              f" via {binding.get('matched_field')}")
    print("\n把它填进 PreparedNCWUSubmission.item_binding_sha256 即完成绑定：")
    print(f"  item_binding_sha256 = \"{digest}\"")
    return 0


SYNTHETIC_HAR = {
    "log": {
        "version": "1.2",
        "entries": [
            {
                "startedDateTime": "2026-11-23T08:00:03.512+08:00",
                "request": {
                    "method": "POST",
                    "url": "https://jwmis.example.edu.cn/hsjw/STU_ElectCourseAction.do",
                    "headers": [
                        {"name": "Content-Type",
                         "value": "application/x-www-form-urlencoded; charset=GBK"},
                        {"name": "Referer",
                         "value": "https://jwmis.example.edu.cn/hsjw/STU_ElectCourse.jsp"},
                    ],
                    "postData": {
                        "mimeType": "application/x-www-form-urlencoded",
                        "text": "xh=20260001&xn=2026&xq=1&lcid=S202&xktype=2"
                                "&hidOption=9f2c31ab77&items=3141592653589",
                    },
                },
                "response": {"status": 200, "headers": [],
                             "content": {"text": "flag:1"}},
            }
        ],
    }
}

SYNTHETIC_CATALOG = [
    {"course_id": "K0012345", "class_id": "3141592653589",
     "skbjdm": "3141592653589", "skbh": "(1)", "remain": 3, "name": "示例课程"},
]


def cmd_selftest(args) -> int:
    work = (Path(args.workdir) if args.workdir
            else Path(tempfile.mkdtemp(prefix="p0_selftest_")))
    work.mkdir(parents=True, exist_ok=True)
    har_path = work / "synthetic.har"
    har_path.write_text(json.dumps(SYNTHETIC_HAR, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    catalog_path = work / "catalog.json"
    catalog_path.write_text(json.dumps(SYNTHETIC_CATALOG, ensure_ascii=False, indent=2),
                            encoding="utf-8")

    har = load_har(har_path)
    samples = collect_samples(har)
    assert len(samples) == 1, f"样本数应为 1，实际 {len(samples)}"
    sample = samples[0]
    ok, problems = validate_sample(sample)
    assert ok, f"合成样本未通过校验: {problems}"
    assert sample.field("items") == "3141592653589", sample.field("items")
    assert sample.field("hidOption") == "9f2c31ab77"

    catalog = load_catalog(catalog_path)
    binding = bind_teaching_class(sample.field("items"), catalog)
    assert binding and binding["matched_field"] == "skbjdm", f"绑定失败: {binding}"

    out = work / "evidence"
    result = write_outputs(sample, sha256_file(har_path), har_path, catalog,
                           catalog_path, out, Path(args.repo), "selftest", 30)
    binding_file = Path(result["binding"])
    digest = sha256_file(binding_file)
    assert digest == result["binding_sha256"], "证据哈希与回报不一致"
    payload = json.loads(binding_file.read_text(encoding="utf-8"))
    assert payload["mapping_proven"] is True
    assert Path(result["contract"]).is_file()
    assert result["attest_script"] and Path(result["attest_script"]).is_file()

    # 负例：items 含 '|' 必须被拒
    bad = SubmitSample(
        started_at="x", url="u", origin="o", status=200,
        fields=[("xh", "1"), ("xn", "2026"), ("xq", "1"), ("lcid", "S202"),
                ("xktype", "2"), ("hidOption", "a"), ("items", "1|2")],
        referer="", content_type="")
    ok_bad, problems_bad = validate_sample(bad)
    assert not ok_bad and any("|" in p for p in problems_bad), problems_bad

    print("PASS selftest")
    print(f"  samples=1  binding={bindings_str(binding)}  sha256={digest[:16]}...")
    print(f"  outputs -> {out}")
    return 0


def bindings_str(binding: dict) -> str:
    return f"{binding['course_id']}/{binding['class_id']}@{binding['matched_field']}"


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="P0 写协议还原：从真实 HAR 还原 S202 选课写入合同")
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="解析 HAR 并生成证据")
    scan.add_argument("--har", required=True)
    scan.add_argument("--catalog", default=None,
                      help="选课目录 JSON（含 skbjdm/class_id），用于绑定内部教学班")
    scan.add_argument("--out", default="evidence")
    scan.add_argument("--repo", default=".",
                      help="kingo-assistant 仓库根目录（写 attestation 脚本用）")
    scan.add_argument("--reviewer", default="operator")
    scan.add_argument("--days", type=int, default=30)
    scan.add_argument("--print-form", action="store_true",
                      help="打印未脱敏表单（含个人信息）")
    scan.set_defaults(func=cmd_scan)

    verify = sub.add_parser("verify", help="复核证据哈希")
    verify.add_argument("--binding", required=True)
    verify.add_argument("--sha", default=None)
    verify.set_defaults(func=cmd_verify)

    selftest = sub.add_parser("selftest", help="内置合成抓包自检")
    selftest.add_argument("--workdir", default=None,
                          help="默认写入系统临时目录，不污染仓库")
    selftest.add_argument("--repo", default=".")
    selftest.set_defaults(func=cmd_selftest)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
