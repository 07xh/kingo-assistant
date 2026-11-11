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


class Scheduler:
    def __init__(self, client, clock, config):
        self.client = client
        self.clock = clock
        self.config = config
        self.strategy = config["strategy"]
        self.cycles = 0
        self.last_error: str | None = None
        self.states = {item["key"]: "待监测" for item in config["courses"]}
        state_file = self.strategy.get("state_file", "")
        self._state_store = MonitorStateStore(state_file) if state_file else None
        remembered = self._state_store.load() if self._state_store else {}
        self._already_notified = {key: value for key, value in remembered.items()
                                  if key in self.states}

    @staticmethod
    def matches(target: dict, item) -> bool:
        for key in ("course_id", "class_id", "course_name", "teacher"):
            needle = target.get(key)
            if not needle:
                continue
            value = getattr(item, key, None)
            if value is None:
                return False
            if key in ("course_id", "class_id"):
                if str(needle).strip() != str(value).strip():
                    return False
            elif str(needle).strip().casefold() not in str(value).casefold():
                return False
        return True

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
        previous = self._already_notified.copy()
        for target in self.config["courses"]:
            key = target["key"]
            matching = [course for course in catalog if self.matches(target, course)]
            if len(matching) > 1:
                self.states[key] = Status.AMBIGUOUS.value
                self._already_notified.pop(key, None)
                continue
            if not matching:
                self.states[key] = Status.MISSING.value
                self._already_notified.pop(key, None)
                continue
            item = matching[0]
            if item.remain is None:
                self.states[key] = Status.UNKNOWN.value
                self._already_notified.pop(key, None)
            elif item.remain <= 0:
                self.states[key] = Status.FULL.value
                self._already_notified.pop(key, None)
            else:
                self.states[key] = f"{Status.AVAILABLE.value}（{item.remain}）"
                fingerprint = MonitorStateStore.fingerprint(item.course_id, item.class_id)
                if self._already_notified.get(key) != fingerprint:
                    notifications.append((key, fingerprint, target, item))

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
                self._already_notified[key] = fingerprint

        # Persist only successful provider-accepted notification fingerprints.
        # Full/unknown/missing classes rearm previously delivered notifications.
        if self._state_store and opted_in and self._already_notified != previous:
            self._state_store.save(self._already_notified)

    async def _sleep_or_stop(self, seconds: float) -> bool:
        try:
            await asyncio.wait_for(self.client.stop.wait(), timeout=seconds)
            return False
        except asyncio.TimeoutError:
            return True

    async def run(self) -> None:
        try:
            mode = self.strategy["mode"]
            if mode == "burst":
                logger.info("计划时间检查：只执行一次课程余量读取，不发送抢课 POST")
                await self.clock.wait_until(self.strategy["target_time"], advance_ms=0)
                if not self.client.stop.is_set():
                    await self.cycle()
                return
            # Offline schedulers in tests need no host; real fast-mode policy
            # still validates an actual origin in the live KingoClient.
            limits = validate_limits(self.strategy, self.config.get("base_url", ""))
            deadline = time.monotonic() + limits.max_duration_minutes * 60
            while not self.client.stop.is_set():
                # A zero cycle limit permits long monitoring, but never an
                # unbounded unattended session: deadline ALWAYS applies.
                if (limits.max_cycles and self.cycles >= limits.max_cycles) or time.monotonic() >= deadline:
                    break
                cycle_started = time.monotonic()
                await self.cycle()
                if limits.max_cycles and self.cycles >= limits.max_cycles:
                    break
                seconds_left = max(0.0, deadline - time.monotonic())
                if seconds_left <= 0:
                    break
                elapsed = time.monotonic() - cycle_started
                # Pace from cycle starts without compensating with request bursts.
                remaining = max(0.0, random.uniform(limits.low, limits.high) - elapsed)
                if remaining and not await self._sleep_or_stop(min(remaining, seconds_left)):
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
