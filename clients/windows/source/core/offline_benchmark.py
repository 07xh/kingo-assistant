"""Local-only reference scheduler. No network and no school integration.

1.5 s / 2 workers are observed default settings in the previously shared
North Timetable APK; they are not a proven maximum or an authorized rate.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from statistics import mean

REFERENCE_INTERVAL_SECONDS = 1.5
REFERENCE_WORKERS = 2


@dataclass(frozen=True)
class BenchmarkResult:
    interval_seconds: float
    workers: int
    cycles_per_worker: int
    started: int
    finished: int
    max_concurrent: int
    mean_interval_ms: float
    worst_lateness_ms: float
    simulated_processing_ms: float

    @property
    def reference_dispatches_per_second(self) -> float:
        """Theoretical local dispatch rate, NOT measured HTTP throughput."""
        return self.workers / self.interval_seconds


def format_benchmark(result: BenchmarkResult) -> str:
    return (
        "北化课表参数 · 本机离线调度对标（不是实测抢课速度）\n"
        f"参考间隔：{result.interval_seconds:g} 秒/工作线程\n"
        f"工作线程：{result.workers}（asyncio 模拟任务）\n"
        f"模拟启动/完成：{result.started}/{result.finished} 次\n"
        f"模拟最大同时执行：{result.max_concurrent}\n"
        f"同一工作线程平均启动间隔：{result.mean_interval_ms:.1f} ms\n"
        f"最大发起延迟：{result.worst_lateness_ms:.1f} ms\n"
        f"理论调度事件速率：{result.reference_dispatches_per_second:.2f} 次/秒（并非 HTTP RPS）\n\n"
        "所有任务都在本机 asyncio 事件循环中模拟；没有发起 HTTP 请求。\n"
        "北化课表的真实最大速率、学校允许的频率及选课成功率均未验证。\n"
        "实际校园查询仍保持 60–3600 秒的间隔限制。"
    )


async def run_offline_benchmark(
    *,
    interval_seconds: float = REFERENCE_INTERVAL_SECONDS,
    workers: int = REFERENCE_WORKERS,
    cycles_per_worker: int = 4,
    simulated_processing_ms: float = 120.0,
) -> BenchmarkResult:
    """Schedule local dummy tasks against monotonic deadlines.

    Does not instantiate an HTTP client, read credentials or touch the live
    scheduler. Each worker models repeated local work, not school requests.
    """
    if not (isinstance(interval_seconds, (int, float)) and
            not isinstance(interval_seconds, bool) and
            0.01 <= interval_seconds <= 60):
        raise ValueError("离线模拟间隔须在 0.01–60 秒之间")
    if type(workers) is not int or not 1 <= workers <= 4:
        raise ValueError("离线模拟工作线程须为 1–4")
    if type(cycles_per_worker) is not int or not 2 <= cycles_per_worker <= 10:
        raise ValueError("离线模拟每线程周期须为 2–10")
    if not (isinstance(simulated_processing_ms, (int, float)) and
            not isinstance(simulated_processing_ms, bool) and
            0 <= simulated_processing_ms <= 1000):
        raise ValueError("模拟处理时间须为 0–1000 毫秒")

    loop = asyncio.get_running_loop()
    t0 = loop.time() + 0.01
    starts: list[list[float]] = [[] for _ in range(workers)]
    lateness: list[float] = []
    in_flight = 0
    max_in_flight = 0
    completed = 0

    async def simulate_worker(index: int) -> None:
        nonlocal in_flight, max_in_flight, completed
        for cycle in range(cycles_per_worker):
            due = t0 + cycle * interval_seconds
            await asyncio.sleep(max(0.0, due - loop.time()))
            now = loop.time()
            starts[index].append(now)
            lateness.append(max(0.0, now - due) * 1000)
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
            try:
                await asyncio.sleep(simulated_processing_ms / 1000)
            finally:
                in_flight -= 1
            completed += 1

    await asyncio.gather(*(simulate_worker(index) for index in range(workers)))
    actual_intervals = [
        (times[i] - times[i - 1]) * 1000
        for times in starts for i in range(1, len(times))
    ]
    return BenchmarkResult(
        interval_seconds=float(interval_seconds), workers=workers,
        cycles_per_worker=cycles_per_worker,
        started=sum(map(len, starts)), finished=completed,
        max_concurrent=max_in_flight,
        mean_interval_ms=mean(actual_intervals),
        worst_lateness_ms=max(lateness),
        simulated_processing_ms=float(simulated_processing_ms),
    )


if __name__ == "__main__":
    print(format_benchmark(asyncio.run(run_offline_benchmark())))
