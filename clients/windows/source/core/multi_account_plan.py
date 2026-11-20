"""Per-account plan fan-out for multi-target pickup (offline validation only).

``GlobalRequestBudget`` serialises requests that share ONE database path. Two
identities must therefore never share a budget file: the sliding window would
make account B wait for account A, and the policy signature check would reject
the second window set outright. This module makes that precondition explicit and
checkable before any socket is opened.

What it refuses, and why:

* two accounts on the same ``database`` -> serialises the wrong thing;
* the same ``(course_id, class_id)`` in two accounts -> a duplicate-booking race
  that the journal cannot deduplicate across accounts;
* a relative or ``:memory:`` database -> the cross-process lease degenerates;
* a proxy URL with no scheme, or a non-http(s)/socks5 scheme -> silently
  inheriting the OS proxy is how accounts leak onto the same egress;
* a target without a class id -> the write contract needs the internal class.

No network, no file I/O beyond path normalisation, no credentials stored.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping
from urllib.parse import urlsplit

#: Schemes a per-account egress may use. Anything else falls back to the OS
#: proxy, which defeats the point of a per-account egress.
ALLOWED_PROXY_SCHEMES = ("http", "https", "socks5", "socks5h")


class PlanError(ValueError):
    """The multi-account plan would violate a budget or target invariant."""


@dataclass(frozen=True, slots=True, repr=False)
class AccountTarget:
    course_id: str
    class_id: str
    key: str = ""

    def __post_init__(self) -> None:
        for value, label in ((self.course_id, "课程代码"), (self.class_id, "教学班 ID")):
            if (not isinstance(value, str) or not 1 <= len(value.strip()) <= 100
                    or any(ord(ch) < 32 for ch in value)):
                raise PlanError(f"{label}必须是非空可打印字符串")

    @property
    def fingerprint(self) -> tuple[str, str]:
        return (self.course_id.strip(), self.class_id.strip())

    def as_dict(self) -> dict:
        return {"key": self.key, "course_id": self.course_id,
                "class_id": self.class_id}

    def __repr__(self) -> str:
        return f"AccountTarget({self.course_id}/{self.class_id})"


@dataclass(frozen=True, slots=True, repr=False)
class AccountPlan:
    """One identity's independent budget file, egress and targets."""

    name: str
    database: Path
    origin: str
    targets: tuple[AccountTarget, ...]
    proxy: str = ""
    windows: tuple[tuple[float, int], ...] = ()
    max_concurrency: int = 1
    policy_source: str = ""
    notes: str = field(default="")

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not 1 <= len(self.name.strip()) <= 60:
            raise PlanError("账号名称必须是 1–60 个字符")
        if not isinstance(self.database, Path):
            raise PlanError("预算数据库必须是 Path")
        if not self.targets:
            raise PlanError(f"账号 {self.name} 没有目标")
        if type(self.max_concurrency) is not int or not 1 <= self.max_concurrency <= 16:
            # Each account owns a separate database file, so >1 here is a
            # per-account burst, not a shared-lease violation. The ceiling is
            # the same one ``RequestPolicy`` enforces.
            raise PlanError("账号并发数必须是 1–16 的整数")

    @property
    def egress(self) -> str:
        return self.proxy or "os-default"

    def as_dict(self) -> dict:
        """Credential-free summary: the database path is a local absolute path."""
        return {
            "name": self.name,
            "database": str(self.database),
            "origin": self.origin,
            "proxy": self.egress,
            "max_concurrency": self.max_concurrency,
            "policy_source": self.policy_source,
            "targets": [target.as_dict() for target in self.targets],
            "windows": [list(window) for window in self.windows],
        }

    def __repr__(self) -> str:
        return (f"AccountPlan({self.name}, targets={len(self.targets)}, "
                f"egress={self.egress})")


def _checked_proxy(raw: Any, name: str) -> str:
    if raw in (None, ""):
        return ""
    if not isinstance(raw, str) or len(raw) > 300:
        raise PlanError(f"账号 {name} 的代理地址无效")
    split = urlsplit(raw)
    if split.scheme.lower() not in ALLOWED_PROXY_SCHEMES:
        raise PlanError(
            f"账号 {name} 的代理协议必须是 {'/'.join(ALLOWED_PROXY_SCHEMES)}")
    if not split.hostname:
        raise PlanError(f"账号 {name} 的代理缺少主机名")
    return raw


def _checked_database(raw: Any, name: str) -> Path:
    if not isinstance(raw, str) or not raw.strip() or len(raw) > 500:
        raise PlanError(f"账号 {name} 缺少预算数据库路径")
    if raw.strip() == ":memory:":
        raise PlanError(f"账号 {name} 的预算数据库不能是 :memory:（跨进程锁失效）")
    path = Path(raw.strip())
    if not path.is_absolute():
        raise PlanError(f"账号 {name} 的预算数据库必须是绝对路径")
    if path.name in ("", ".", ".."):
        raise PlanError(f"账号 {name} 的预算数据库文件名无效")
    return path


def _checked_windows(raw: Any, name: str) -> tuple[tuple[float, int], ...]:
    if raw in (None, ()):
        return ()
    if not isinstance(raw, (list, tuple)) or not 1 <= len(raw) <= 8:
        raise PlanError(f"账号 {name} 必须配置 1–8 个请求窗口")
    windows: list[tuple[float, int]] = []
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            raise PlanError(f"账号 {name} 的请求窗口必须是 [秒数, 请求数]")
        seconds, count = item
        if (type(seconds) not in (int, float) or isinstance(seconds, bool)
                or not 0 < float(seconds) <= 86400):
            raise PlanError(f"账号 {name} 的窗口长度无效")
        if type(count) is not int or not 1 <= count <= 10_000:
            raise PlanError(f"账号 {name} 的窗口请求数无效")
        windows.append((float(seconds), count))
    if len({w[0] for w in windows}) != len(windows):
        raise PlanError(f"账号 {name} 存在重复窗口长度")
    return tuple(windows)


def _account_sections(config: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Accept either ``accounts: [...]`` or a bare single-account mapping."""
    accounts = config.get("accounts")
    if accounts is None:
        return [config]
    if not isinstance(accounts, list) or not accounts:
        raise PlanError("accounts 必须是非空数组")
    for entry in accounts:
        if not isinstance(entry, Mapping):
            raise PlanError("accounts 的每一项必须是映射")
    return list(accounts)


def build_account_plans(config: Mapping[str, Any]) -> list[AccountPlan]:
    """Validate a multi-account config and fan it out into independent plans.

    The shared ``server_policy`` block supplies origin/windows defaults; each
    account may override ``database``/``proxy``/``targets`` but never the origin.
    """
    if not isinstance(config, Mapping):
        raise PlanError("配置必须是映射")
    shared_policy = config.get("server_policy") if isinstance(
        config.get("server_policy"), Mapping) else {}
    shared_origin = config.get("base_url") or shared_policy.get("origin") or ""
    if not isinstance(shared_origin, str) or not shared_origin.strip():
        raise PlanError("缺少学校来源（base_url 或 server_policy.origin）")
    shared_origin = shared_origin.strip().rstrip("/")
    plan_origin = urlsplit(shared_origin)
    if plan_origin.scheme != "https" or not plan_origin.hostname:
        raise PlanError("学校来源必须是 https URL")

    plans: list[AccountPlan] = []
    seen_databases: dict[str, str] = {}
    seen_targets: dict[tuple[str, str], str] = {}
    seen_proxies: dict[str, str] = {}
    seen_names: set[str] = set()

    for index, section in enumerate(_account_sections(config), start=1):
        name = str(section.get("name") or f"account{index}").strip()
        if name in seen_names:
            raise PlanError(f"账号名称重复：{name}")
        seen_names.add(name)
        origin = section.get("base_url") or section.get("origin") or shared_origin
        if not isinstance(origin, str) or origin.strip().rstrip("/") != shared_origin:
            raise PlanError(f"账号 {name} 的学校来源与共享策略不一致")
        database = _checked_database(
            section.get("database") or shared_policy.get("database"), name)
        key = str(database).casefold()
        if key in seen_databases:
            raise PlanError(
                f"账号 {name} 与 {seen_databases[key]} 共用预算数据库，会被串行化")
        seen_databases[key] = name
        proxy = _checked_proxy(section.get("proxy"), name)
        if proxy:
            proxy_key = proxy.casefold()
            if proxy_key in seen_proxies:
                raise PlanError(
                    f"账号 {name} 与 {seen_proxies[proxy_key]} 共用出口代理")
            seen_proxies[proxy_key] = name
        windows = _checked_windows(
            section.get("windows") or shared_policy.get("windows"), name)
        raw_targets = section.get("targets") or section.get("courses")
        if not isinstance(raw_targets, list) or not raw_targets:
            raise PlanError(f"账号 {name} 没有配置目标")
        targets: list[AccountTarget] = []
        for raw in raw_targets:
            if not isinstance(raw, Mapping):
                raise PlanError(f"账号 {name} 的目标必须是映射")
            target = AccountTarget(
                course_id=str(raw.get("course_id") or ""),
                class_id=str(raw.get("class_id") or ""),
                key=str(raw.get("key") or ""),
            )
            owner = seen_targets.get(target.fingerprint)
            if owner is not None:
                raise PlanError(
                    f"目标 {target.course_id}/{target.class_id} 同时出现在 "
                    f"{owner} 与 {name}，会产生重复提交")
            seen_targets[target.fingerprint] = name
            targets.append(target)
        plans.append(AccountPlan(
            name=name,
            database=database,
            origin=shared_origin,
            targets=tuple(targets),
            proxy=proxy,
            windows=windows,
            max_concurrency=section.get("max_concurrency", 1),
            policy_source=str(shared_policy.get("source") or ""),
            notes=str(section.get("notes") or ""),
        ))
    return plans


def summarize(plans: Iterable[AccountPlan]) -> dict:
    """Credential-free aggregate for the UI and for log lines."""
    plans = list(plans)
    return {
        "accounts": len(plans),
        "targets": sum(len(plan.targets) for plan in plans),
        "distinct_databases": len({str(p.database).casefold() for p in plans}),
        "distinct_egress": len({p.egress for p in plans}),
        "per_account": [plan.as_dict() for plan in plans],
    }
