"""Local-only startup readiness analysis; never probes university endpoints.

A status of READY means only the offline prerequisites have passed.  It is not
proof of the school's session validity, seat availability, or permission to run
an automated query.  Manual timetable hints are warnings, not official rules.
"""
from __future__ import annotations

from dataclasses import dataclass
import os

from core.conflicts import find_conflicts
from core.monitor_policy import validate_limits
from core.request_budget import PolicyError, read_verified_policy_config
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Check:
    label: str
    passed: bool
    detail: str
    severity: str = "blocker"  # blocker for local start; warning for advice only


def split_checks(checks: list[Check]) -> tuple[list[Check], list[Check]]:
    """Return blocking failures and advisory warnings, in display order."""
    blockers = [c for c in checks if not c.passed and c.severity == "blocker"]
    warnings = [c for c in checks if not c.passed and c.severity == "warning"]
    return blockers, warnings


def format_checks(checks: list[Check], *, max_detail: int = 4) -> str:
    """Bounded redacted report suitable for a Windows message dialog."""
    blockers, warnings = split_checks(checks)
    headline = (f"本地检查：{len(checks) - len(blockers) - len(warnings)}/{len(checks)} 项通过；"
                f"{len(blockers)} 项阻止启动，{len(warnings)} 项提示。")
    lines = [headline, "仅检查本机配置；不代表学校接口已真实验收。"]
    for severity, heading in (("blocker", "必须修正"), ("warning", "建议核对")):
        failed = [c for c in checks if not c.passed and c.severity == severity]
        if failed:
            lines.extend(("", heading + "："))
            for item in failed[:max_detail]:
                lines.append("• " + item.label + "：" + item.detail)
            if len(failed) > max_detail:
                lines.append(f"• 还有 {len(failed) - max_detail} 项，请检查配置")
    if not blockers:
        lines.append("\n本机必需项通过；如正式启动，仍须在学校允许的条件下查询。")
    return "\n".join(lines)


def inspect_readiness(config: dict, env: dict | None = None) -> list[Check]:
    environment = os.environ if env is None else env
    protocol = config.get('protocol', {})
    auth = config.get('auth', {})
    notify = config.get('notify', {})
    courses = config.get('courses', [])
    fields = ('identity_path', 'identity_referer', 'identity_selector',
              'entry', 'entry_referer', 'list', 'term_field', 'columns')
    verified = protocol.get('verified') is True
    has_auth = bool(environment.get('KINGO_COOKIE') or auth.get('cookie'))
    all_paths = all(bool(protocol.get(field)) for field in fields) and bool(protocol.get('table_marker'))
    valid_courses = isinstance(courses, list) and bool(courses) and all(isinstance(c, dict) for c in courses)
    placeholders = any('FILL_FROM_' in str(c.get('course_id', '')) or
                       'SECOND_OPTION_ID' in str(c.get('course_id', '')) for c in courses) if valid_courses else True
    missing_identity = (not valid_courses or any(
        not any(str(c.get(f) or '').strip() for f in ('course_id', 'course_name', 'teacher'))
        for c in courses))
    generic_match = valid_courses and any(
        not str(c.get('course_id') or '').strip() and not str(c.get('class_id') or '').strip()
        for c in courses)

    basic = [
        Check('目标课程', valid_courses and not placeholders and not missing_identity,
              '请填写真实课程，不能保留模板代码或空课程'),
        Check('真实只读协议', verified and all_paths,
              '学校只读接口和表格标识尚未完成本人核对；不能启动真实监测'),
        Check('本人登录会话', has_auth,
              '未提供本人官方登录会话；推荐使用环境变量 KINGO_COOKIE'),
        Check('安全运行模式', config.get('strategy', {}).get('dry_run') is True,
              '仅允许 dry_run: true，禁止执行选课写入'),
        Check('本机配置不含会话敏感值', not bool(auth.get('cookie')),
              'YAML 包含 Cookie，建议清除并通过环境变量使用', 'warning'),
        Check('课程精确匹配', not generic_match,
              '部分目标只填名称/教师，可能对应多个教学班，请核对课程代码和班级', 'warning'),
    ]
    try:
        parsed = urlsplit(str(config.get('base_url', '')))
        read_verified_policy_config(config, (parsed.scheme, parsed.netloc))
        basic.append(Check('服务器请求预算', True, '已填写本机策略，仍须由学校独立核对允许范围'))
    except (PolicyError, TypeError, ValueError):
        basic.append(Check('服务器请求预算', False,
                           '缺少已确认的官方访问策略、窗口配额、匹配域名或持久化数据库路径'))
    try:
        validate_limits(config.get('strategy', {}))
    except ValueError:
        basic.append(Check('监测频率与时长', False, '配置的间隔、次数或截止时长不符合本地规则'))
    try:
        conflicts, missing = find_conflicts(courses) if valid_courses else ([], [])
        clash_description = ('、'.join(f'{c.first_key}/{c.second_key}:周{c.day}第{c.first_period}-{c.last_period}节'
                                     for c in conflicts[:3]))
        basic.append(Check('手动节次冲突', not conflicts,
                           ('可能重叠：' + clash_description + (' 等' if len(conflicts) > 3 else ''))
                           if conflicts else '没有发现手动输入的节次重叠', 'warning'))
        basic.append(Check('节次信息完整度', not missing,
                           f'{len(missing)} 门课未录入节次，无法进行冲突初筛', 'warning'))
    except (ValueError, KeyError, TypeError):
        basic.append(Check('手动节次格式', False,
                           '本地节次格式无效，仅影响人工冲突初筛；请在课程编辑器修改', 'warning'))
    if notify.get('enabled'):
        have_notification = bool(environment.get('PUSHPLUS_TOKEN') or environment.get('SERVERCHAN_TOKEN')
                                 or notify.get('pushplus_token') or notify.get('serverchan_token'))
        basic.append(Check('通知渠道', have_notification,
                           '未配置推送令牌，仅可显示本机状态' if not have_notification
                           else '已配置推送令牌（不保证送达）', 'warning'))
    return basic


def inspect_enrollment_schedule(offering, snapshot, semester: str) -> Check:
    """Officially verified *offline contract* for the enrollment preflight.

    This does not perform HTTP calls. Manual timetable hints are insufficient
    to assert that a school will accept a write.
    """
    from core.course_models import (
        ConflictVerdict, CourseOffering, ScheduleSnapshot, time_conflict,
    )

    if (not isinstance(offering, CourseOffering)
            or not isinstance(snapshot, ScheduleSnapshot)
            or snapshot.verified is not True
            or snapshot.semester != semester
            or offering.semester != semester):
        return Check("教学时间冲突", False, "无法确认同一学期的可信课表")
    verdict = time_conflict(offering.time_slots, snapshot.slots)
    if verdict is ConflictVerdict.CONFLICT:
        return Check("教学时间冲突", False, "已发现课程时间重叠")
    if verdict is ConflictVerdict.UNKNOWN:
        return Check("教学时间冲突", False, "周次或课程时间不完整，禁止自动提交")
    return Check("教学时间冲突", True, "经核验的时间槽没有重叠")
