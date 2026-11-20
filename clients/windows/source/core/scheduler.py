"""One-read-per-cycle, strictly read-only elective watcher.

The legacy 'burst' setting is interpreted as one scheduled read-only check,
never staggered POST submissions. A warning/notification is not enrollment.
"""
from __future__ import annotations

import asyncio
import os
import random
import time
from collections import defaultdict

import httpx
from loguru import logger

from .client import AuthenticationError, ProtocolError, RateLimitError, Status
from .monitor_state import MonitorStateStore
from .monitor_policy import validate_limits
from .adaptive_read_cadence import AdaptiveReadCadence
from .selection_optim import PollingSchedule
from .release_watch import (
    NotificationLedger, WatchState, apply_observations, observe_all, target_matches,
)

#: Watch classification -> the user-facing Status string defined in client.py.
_STATE_STATUS = {
    WatchState.AMBIGUOUS: Status.AMBIGUOUS,
    WatchState.MISSING: Status.MISSING,
    WatchState.UNKNOWN: Status.UNKNOWN,
    WatchState.FULL: Status.FULL,
}


class Scheduler:
    def __init__(self, client, clock, config):
        self.client = client
        self.clock = clock
        self.config = config
        self.strategy = config["strategy"]
        # Opt-in slowdown of authorized read-only polling; never changes HTTP policy.
        self.adaptive = AdaptiveReadCadence(self.strategy.get("adaptive_polling"))
        self.cycles = 0
        self.last_error: str | None = None
        # Optional pickup schedule. Absent or disabled keeps the original random
        # jitter, so existing deployments behave exactly as before.
        pickup_cfg = self.strategy.get("pickup") or {}
        self.pickup = (PollingSchedule.from_config(self.strategy)
                       if isinstance(pickup_cfg, dict) and pickup_cfg.get("enabled") is True
                       else None)
        self.states = {item["key"]: "待监测" for item in config["courses"]}
        state_file = self.strategy.get("state_file", "")
        self._state_store = MonitorStateStore(state_file) if state_file else None
        remembered = self._state_store.load() if self._state_store else {}
        self._ledger = NotificationLedger(
            {key: value for key, value in remembered.items() if key in self.states})
        # Kept as the same dict object so save/compare paths stay unchanged.
        self._already_notified = self._ledger.active

    @staticmethod
    def matches(target: dict, item) -> bool:
        """Kept for callers and tests; the rule now lives in release_watch."""
        return target_matches(target, item)

    async def notify(self, target: dict, item) -> bool:
        """True only after at least one push endpoint accepts a notification.

        HTTP acceptance is not proof of final delivery to the user's device.
        A failed or unconfigured push must not consume a future alert.
        """
        notify = self.config.get("notify", {})
        if notify.get("enabled") is not True:
            return False
        title = "选课余量提醒（请官网核实）"
        content = f"{item.course_name} / 课程代码 {item.course_id}：检测到余量 {item.remain}"
        if notify.get("include_teacher") is True:
            content += f" / {item.teacher}"
        token = os.getenv("PUSHPLUS_TOKEN") or notify.get("pushplus_token")
        serverchan = os.getenv("SERVERCHAN_TOKEN") or notify.get("serverchan_token")
        jobs = []
        if token:
            jobs.append(("https://www.pushplus.plus/send", {"token": token, "title": title, "content": content}))
        if serverchan and str(serverchan).isalnum():
            jobs.append((f"https://sctapi.ftqq.com/{serverchan}.send", {"title": title, "desp": content}))
        if not jobs:
            logger.warning("已启用通知但未配置有效推送通道，下一轮仍可提醒")
            return False
        accepted = False
        async with httpx.AsyncClient(timeout=8, follow_redirects=False) as http:
            for url, data in jobs:
                try:
                    result = await http.post(url, data=data)
                    result.raise_for_status()
                    # Only a provider-declared success is accepted. A 200 HTML
                    # login/WAF page is not proof the push service accepted it.
                    if "json" not in result.headers.get("content-type", "").lower():
                        logger.warning("通知平台未返回确认 JSON，本次不记录已提醒")
                        continue
                    try:
                        payload = result.json()
                    except ValueError:
                        logger.warning("通知接口返回不可识别 JSON，本次不确认发送")
                        continue
                    if not isinstance(payload, dict) or str(payload.get("code")) not in ("0", "200"):
                        logger.warning("通知平台未确认请求成功")
                        continue
                    accepted = True
                except httpx.HTTPError:
                    logger.warning("通知服务不可用，未标记通知已成功发送")
        return accepted

    async def cycle(self) -> None:
        self.cycles += 1
        target_ids = [str(c["course_id"]) for c in self.config["courses"] if c.get("course_id")]
        # ONE read for ALL configured targets: no redundant per-course requests.
        catalog = await self.client.fetch_course_list(
            self.config["xnxq"], target_ids if len(target_ids) == len(self.config["courses"]) else None)
        notifications = []
        target_list = self.config["courses"]
        observations = observe_all(target_list, catalog,
                                   fingerprint_of=MonitorStateStore.fingerprint)
        previous = self._ledger.snapshot()
        for observation in observations:
            if observation.state is WatchState.AVAILABLE:
                self.states[observation.key] = (
                    f"{Status.AVAILABLE.value}（{observation.remain}）")
            else:
                self.states[observation.key] = _STATE_STATUS[observation.state].value
        # Rearming happens here; marking waits for delivery confirmation below.
        notifications = apply_observations(target_list, observations, self._ledger)

        # Mark an alert delivered only after the notification channel accepts it.
        # Disabled notifications still get local in-memory de-duplication, but
        # never consume future notifications across process restarts.
        opted_in = self.config.get("notify", {}).get("enabled") is True
        for key, fingerprint, target, item in notifications:
            logger.info("余量变化：课程 {} / {}，请在官网手动确认", item.course_id, key)
            try:
                outcome = await self.notify(target, item)
            except Exception as error:
                logger.warning("通知失败：{}；本机状态保留，下一轮可重试", type(error).__name__)
                outcome = False
            if not opted_in or outcome is True:
                self._ledger.mark(key, fingerprint)

        # Persist only successful provider-accepted notification fingerprints.
        # Full/unknown/missing classes rearm previously delivered notifications.
        if self._state_store and opted_in and self._ledger.snapshot() != previous:
            self._state_store.save(self._ledger.snapshot())

    async def _sleep_or_stop(self, seconds: float) -> bool:
        try:
            await asyncio.wait_for(self.client.stop.wait(), timeout=seconds)
            return False
        except asyncio.TimeoutError:
            return True

    def _next_interval(self, limits) -> float:
        """Random jitter normally; a precise cadence inside a pickup peak.

        A peak window may only tighten polling down to the configured floor and
        may never leave the operator-approved range.
        """
        if self.pickup is None:
            baseline = random.uniform(limits.low, limits.high)
        else:
            baseline = max(limits.low, min(self.pickup.interval(), limits.high))
        return self.adaptive.interval(baseline, floor=limits.low)

    async def run(self) -> None:
        try:
            mode = self.strategy["mode"]
            if mode == "burst":
                logger.info("计划时间检查：只执行一次课程余量读取，不发送抢课 POST")
                await self.clock.wait_until(self.strategy["target_time"], advance_ms=0)
                if not self.client.stop.is_set():
                    await self.cycle()
                return
            limits = validate_limits(self.strategy)
            deadline = time.monotonic() + limits.max_duration_minutes * 60
            while not self.client.stop.is_set():
                # A zero cycle limit permits long monitoring, but never an
                # unbounded unattended session: deadline ALWAYS applies.
                if (limits.max_cycles and self.cycles >= limits.max_cycles) or time.monotonic() >= deadline:
                    break
                await self.cycle()
                # Only successful read timings are evidence of current load.
                # No timing data => leave the previously approved interval alone.
                self.adaptive.observe_success(getattr(self.client, "last_timing", None))
                if limits.max_cycles and self.cycles >= limits.max_cycles:
                    break
                seconds_left = max(0.0, deadline - time.monotonic())
                if seconds_left <= 0:
                    break
                if not await self._sleep_or_stop(min(self._next_interval(limits), seconds_left)):
                    break
        except AuthenticationError:
            self.last_error = "本人会话无效或登录已过期；请在官方页面重新登录并核验授权"
            logger.warning("监测已停止：AuthenticationError")
            self.states = {k: Status.AUTH_EXPIRED.value for k in self.states}
        except RateLimitError:
            self.last_error = "学校返回访问限制（403/429）；已停止，不自动重试"
            logger.warning("监测已停止：RateLimitError")
            self.states = {k: Status.RATE_LIMITED.value for k in self.states}
        except ProtocolError:
            self.last_error = "课程接口响应或页面结构不符合核验配置；已停止读取"
            logger.warning("监测已停止：ProtocolError；请核实页面或编码")
            self.states = {k: Status.READ_ERROR.value for k in self.states}
        except httpx.HTTPError:
            self.last_error = "网络请求失败；请检查网络与学校官网状态"
            logger.warning("监测已停止：HTTPError")
            self.states = {k: Status.READ_ERROR.value for k in self.states}
        except ValueError:
            self.last_error = "本机配置错误；请重新检查配置"
            logger.warning("监测已停止：ValueError")
            self.states = {k: Status.READ_ERROR.value for k in self.states}
        finally:
            self.client.stop.set()
