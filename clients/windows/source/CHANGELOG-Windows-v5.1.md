# KingoAssistant Windows V5.1 — 写入门禁证据化 / 时延埋点 / 策略层合并

## 本轮实际实现

- **写入门禁证据化（核心）**：`core/attestation.py` 新增。四个能力门禁（目录 / 已选 / 窗口 / 写入）不再只靠网关自报 `True`，而是必须同时具备一条**哈希校验、带有效期**的本地证据登记项。证据文件被改动、丢失或过期都会让门禁立刻回到未登记状态。CLI：`python main.py --attest status|issue|revoke`。
  - 这一条直接解掉了此前的自锁：`windows_gui.py` 里 `self._engine_factory = None`，全仓没有任何一行代码能把 `official_write_verified` 置真，写路径在结构上不可达。现在有了合法的置真通道，而 fail-closed 语义不变（`attestations=None` 仍然拒绝）。
- **时延埋点**：`core/latency.py` 新增。每次只读读取写一行 JSONL：`budget_wait_ms / request_ms / decode_ms / parse_ms / total_ms`，另附字节数与行数。**不记录课程号、教学班号、课程名、Cookie、请求体、响应体**。文件按 512 KiB 轮转。日志导出时自动附 p50/p95 分布。
  - 分开记 `budget_wait_ms` 是刻意的：本地配额等待和网络慢在总耗时上看起来一样，但修法相反。
- **策略层合并**：`tools/kingo-live/optimizations.py` 改为 `core/selection_optim.py` 的再导出垫片。此前 ClockSync / Prewarmer / ReleaseMonitor / FailureClassifier / FailoverPlanner 五件套在客户端和研究工具里各存一份，会各自漂移。现在只有一份实现，研究 CLI 的 13 项测试直接覆盖客户端代码。
- **拾漏轮询**：`PollingSchedule` + `PeakWindow` 新增，接进 `scheduler.py`。退课高峰窗口内收紧到 30 秒，平时 60 秒；硬性约束在代码里而不是注释里：任何间隔不得低于 30 秒、`peak ≤ offpeak ≤ 3600`、最终间隔被夹进使用者批准的 `interval_range`（高峰只能收紧，不能放宽）。默认关闭，因为华水的高峰时段尚未核实。
- **`ReleaseMonitor` 兼容两种模型**：有 `fingerprint()` 就用（研究模型），没有就用 `course_id\x1fclass_id`（客户端 `CourseOffering`）。`remain is None` 一律按 0 处理，绝不当成有余量。

## 验收

- 本地离线单测：**231 项通过**（共收集 272 项；其余 41 项是需要 `httpx`/`tkinter`/`yaml` 的 GUI/HTTP 模块，本机沙箱无这些依赖，由 CI 覆盖）。新增 80 项：attestation 28、latency 24、selection_optim 28。
- 研究 CLI：`tools/kingo-live/test_optimizations.py` 13 项全通过（走垫片，验证行为与端口前一致）。
- 新增测试全部使用真实临时证据文件，因此哈希校验与过期是**被验证**而不是被绕过。

## 边界（未变化）

- **仍然没有 NCWU 提交写适配器**。四个门禁全部登记为有效也不能提交：提交按钮保持禁用，GUI 只显示 `验收证据 n/4`。
- 没有访问学校、没有重放 HAR、没有发送选课请求、没有绕过验证码或频率限制。
- 30 秒绝对下限、串行写入、结果未知即停 —— 三条全部保留，本轮没有放松任何一条。
- 真实课程目录 / 余量语义 / 窗口边界 / 提交合同仍未核实；需要一份含真实提交成功的 HAR 才能推进。
