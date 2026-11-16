# v1 方案复查纠错记录 · 2026-11-16

对象：`docs/architecture/optimization-plan-v1.md`
复查来源：`KingoAssistant_全面审查与修订方案_v2`（个人空间 doc `SlKWXtC66JfHeXhXdmk5WS`）
核对方式：逐条回到代码与 `tools/bench` 数据验证，不采信任何一方的转述

## 结论

审查提出的 5 条 P0 意见**全部成立**，其中 3 条是 v1 的实质性错误，已修改仓库内
受影响的表述。另 2 条属于「定位夸大」而非事实错误，已改措辞。

v1 中仍然成立的部分：写路径缺口在真实目录 / 余量语义 / 提交契约三件事上；
FSM、EXE 外壳和离线测试不能替代功能闭环。

## 逐条核实

| # | v1 原表述 | 复查结论 | 代码证据 | 处理 |
|---|---|---|---|---|
| 1 | "8 并发成功率从 100% 掉到 12.5%" | **不成立**。`success_rate` 是「确认成功的请求数 / 全部请求数」，不是「学生拿到课的概率」 | `academic_latency_sim.py:513` `len(succ) / len(a)`；`Attempt` 每 session 一条 | 改写为「单请求确认比例」；表格列名、结论段、`do-not-do` 清单全部更正；模拟器字段加口径注释 |
| 2 | "发现时延可优化 100 倍，是主攻方向" | **部分不成立**。100 倍来自 `poll_interval_ms` 60000 vs 500 这两个**模拟输入**；客户端有 30 s 硬下限，且无已核实频率许可 | `scenarios()` 里 `poll_interval_ms=60000.0` / `500.0`；`monitor_policy.ABSOLUTE_MIN_INTERVAL=30.0` | 改为「模拟输入下的跨度」；明确可落地差异是 30 s vs 60 s；不把 500 ms 写成目标 |
| 3 | "总 p95 70ms" | **不成立**。`total_ms` 只加 client+network+queue+tx，不含发现等待与读回 | `academic_latency_sim.py:140` `total_ms` property | 改称「流水线 p95」；`total_ms` 加 docstring 说明排除项 |
| 4 | "`max_concurrency` 还留了口子" | **不成立**。策略层强制 `max_concurrency == 1`，否则抛错 | `request_budget.py:56-57` `if ... != 1: raise PolicyError` | 删除该指控；在 v1 §2 结论里改为「v2 复查确认：不存在留口子」 |
| 5 | "门禁改造是第一里程碑，解锁全部后续工作" | **定位夸大**。attestation 只消除死代码，不构成学校许可，也不能证明接口实测 | `enrollment.py:_gate()` 中 `institution_permits_write` 仍是独立参数、默认 `False`、全仓无置真路径 | v1 §P0-1 标题与正文改写；明确「证据 ≠ 许可」「先只读契约，后写适配器」 |

## 关键澄清（防止把纠错当放松）

第 5 条的纠错不等于承认实现有问题。已落地的 `core/attestation.py`：

* `institution_permits_write` 没有被 attestation 替代，仍默认 `False`、无置真路径；
* 四个证据门禁全绿 + 学校许可 + 用户 opt-in 同时成立才进 `_gate`；
* 当前仓库 **0/4 门禁登记**，且没有任何 NCWU 写适配器，因此写路径仍不可达；
* 自签一份 `reviewer: self` 的证据只能说明「登记过」，`attestation.md` 的
  "Deliberate limits" 一节已明写这一点。

所以第 5 条修正的是**优先级叙事**（先只读、后写入），不是实现语义。

## 修改清单

| 文件 | 改动 |
|---|---|
| `docs/architecture/optimization-plan-v1.md` | §2 加口径警告；两张表列名 `成功率` → `单请求确认比例` / `总 p95` → `流水线 p95`；三条结论重写；§P0-1 标题与正文改写；§4.2 / §4.5 / §4.7 中 3 处 `成功率 12.5%` 表述更正 |
| `docs/architecture/latency-instrumentation.md` | 表格加 `Confirmed / sent` 与 `Pipeline p95` 语义说明；补 30 s 地板与 500 ms 非可部署配置的说明 |
| `README.md` | 「100× / success 1.00→0.125」段重写为准确口径 + 明确 `success_rate` 与端到端时延的区别 |
| `tools/bench/academic_latency_sim.py` | 模块 docstring 增「Metric definitions that are routinely misread」；`total_ms` 加排除项说明；`success_rate` 字段加「不是学生成功率」注释 |

## 未采纳的建议及理由

* **「删除高频轮询落地建议」**：v1 从未落地 500 ms。实际实现是 30 s 硬底 +
  `interval_range` 夹紧 + 默认关闭的高峰窗口，方向与审查一致，无冲突可删。
* **「不把门禁改造列为里程碑」**：接受结论顺序（先只读后写入），但不接受
  「改造等于放松」的推断——见上节澄清。死代码留着不修，任何适配器接进来都会
  在 `_gate()` 处直接抛错。

## 遗留

审查提出的「分开统计单次任务至少一次成功的概率 / 单请求成功比例 / 重复提交数 /
服务拒绝数，并在有背景竞争者、不同分配规则下重复独立种子实验」是**未做**的部分：
模拟器目前只输出单请求比例。要补这层统计，需要修改 `summarize()` 输出结构并重跑
矩阵，属于独立一轮工作，此处仅记录，不在本轮冒充已完成。
