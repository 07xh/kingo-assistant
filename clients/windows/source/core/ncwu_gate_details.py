"""Offline, privacy-safe display of NCWU write capability gates.

This is diagnostic only; local evidence never enables a submission.
"""
from __future__ import annotations

from core.attestation import AttestationError, GATE_NAMES

_LABELS = {
    "official_catalog_verified": "官方课程目录及真实余量",
    "official_enrollment_verified": "本人已选记录与内部教学班",
    "official_window_verified": "选课窗口状态",
    "official_write_verified": "正式提交写入契约",
}
_STATES = {
    "valid": "证据有效",
    "missing": "证据缺失",
    "expired": "证据过期",
    "invalid": "证据无效",
}


def format_ncwu_gate_details(registry, *, adapter_installed: bool) -> str:
    """Explain local blockers without reading account data or contacting NCWU."""
    states = {}
    if registry is not None:
        try:
            report = registry.describe()
            states = {item["gate"]: item["state"] for item in report["rows"]}
        except (AttestationError, KeyError, TypeError, ValueError):
            states = {gate: "invalid" for gate in GATE_NAMES}

    lines = ["本报告只检查本地验收记录，不访问学校。", ""]
    for index, gate in enumerate(GATE_NAMES, 1):
        lines.append(
            f"{index}. {_LABELS[gate]}："
            f"{_STATES.get(states.get(gate, 'missing'), '状态未知')}"
        )
    lines.extend((
        "",
        "自动执行器：" + (
            "已注入，但仍须实机验收" if adapter_installed else "未接入"
        ),
        "当前版本没有已验证的 S202 提交表单提供器。",
        "当前 S202 已选结果解析器只产生候选记录，不能确认成功。",
        "还需核实校方许可、请求限额、内部教学班与提交项的对应关系。",
        "",
        "即使本地证据显示 4/4，也不会据此启用正式选课提交。",
    ))
    return "\n".join(lines)
