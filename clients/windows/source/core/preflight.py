"""Offline checklist of prerequisites. It never probes school endpoints."""
from __future__ import annotations

from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Check:
    label: str
    passed: bool
    detail: str


def inspect_readiness(config: dict, env: dict | None = None) -> list[Check]:
    environment = os.environ if env is None else env
    protocol = config.get('protocol', {})
    auth = config.get('auth', {})
    notify = config.get('notify', {})
    courses = config.get('courses', [])
    fields = ('identity_path', 'identity_referer', 'identity_selector',
              'entry', 'entry_referer', 'list', 'term_field', 'columns')
    verified = protocol.get('verified') is True
    # Intentionally only report presence; never serialize credentials or raw endpoints.
    has_auth = bool(environment.get('KINGO_COOKIE') or auth.get('cookie'))
    all_paths = all(bool(protocol.get(field)) for field in fields) and bool(protocol.get('table_marker'))
    placeholders = any('FILL_FROM_' in str(c.get('course_id', '')) or
                       'SECOND_OPTION_ID' in str(c.get('course_id', '')) for c in courses)
    basic = [
        Check('课程已填写', not placeholders, '请替换模板中的示例课程代码' if placeholders else '未发现默认模板课程代码'),
        Check('真实只读协议', verified and all_paths,
              '尚未核对学校实际只读接口及表格标识' if not (verified and all_paths)
              else '已标记核对；仍需在学校官网人工确认'),
        Check('本人登录会话', has_auth,
              '未提供本人合法登录会话的 KINGO_COOKIE' if not has_auth else '已配置会话（不保证仍有效）'),
        Check('安全运行模式', config.get('strategy', {}).get('dry_run') is True,
              '只读模式已开启' if config.get('strategy', {}).get('dry_run') is True else '只读模式被修改，禁止启动'),
        Check('本机配置不含会话敏感值', not bool(auth.get('cookie')),
              '配置文件含有 Cookie，建议通过环境变量使用' if auth.get('cookie') else '未在 YAML 保存 Cookie'),
    ]
    if notify.get('enabled'):
        have_notification = bool(environment.get('PUSHPLUS_TOKEN') or environment.get('SERVERCHAN_TOKEN')
                                 or notify.get('pushplus_token') or notify.get('serverchan_token'))
        basic.append(Check('通知渠道', have_notification,
                           '没有有效推送令牌，将只显示本机状态' if not have_notification
                           else '已配置推送令牌（不保证送达）'))
    return basic
