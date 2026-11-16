# KingoAssistant 抢课能力评估与优化方案 v1

对象：`github.com/07xh/kingo-assistant`（华北水利水电大学 / 青果教务 · 个人选课助手）  
评估基线：`main@542a528`（PR #41 native signed EXE login），111 个 py 文件 / 约 12.8k 行（含测试），40 篇 md，99 项离线单测  
产出定位：阶段 A 已落地（见 `CHANGELOG-Windows-v5.1.md`：门禁证据化 / 时延埋点 / 策略层合并 / 拾漏轮询）；阶段 B–D 见本文第 3、5 节。

---

## 0. 一句话结论

> 这个项目现在**不是抢课工具，是一套把抢课工具锁死的合规框架**。  
> 编排、状态机、日志、门禁、UI 全都做完了，唯独**最关键的三个数据没拿到**（课程目录端点的响应结构、余量字段语义、提交写合同），于是整条写路径在代码层面**结构性地不可执行**——`windows_gui.py:241` 写着 `self._engine_factory = None`，全仓没有任何一行代码能把 `official_write_verified` 置为 `True`。

真正该发力的地方不是继续加门禁、加测试、加 UI 分页，而是：  
**① 用一次真实窗口的`完整抓包把三个未知变成已知；② 把已经写好但没接线的时延优化层接进去；③ 抢课胜负在「发现时延」，不在「提交并发」——这一点仓库自带的模拟数据已经证明了，但代码没按这个结论写。`**

---

## 1. 现状盘点

### 1.1 事实层

| 项       | 数据                                                                                                                                                                               |
| ------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 语言/框架   | Python 3 + Tkinter GUI + httpx + BeautifulSoup，PyInstaller 打包单文件 EXE                                                                                                             |
| 代码分布    | `clients/windows/source/`（主客户端，`windows_gui.py` 单文件 1850 行）<br />`tools/kingo-live/`（研究用，`live_client.py` 1162 行 + `optimizations.py` 431 行）<br />`tools/bench/`（时延/并发模拟器 743 行） |
| 测试      | 99 项纯离线单测 + 打包 EXE 冒烟 + Tk GUI 冒烟，CI 全绿                                                                                                                                          |
| 版本      | V5.0 在 PR #3 审核中，V4.6 已合 main；最新提交是 #41 原生签名登录                                                                                                                                   |
| Android | 只有 README，无代码                                                                                                                                                                    |
| 许可      | 无（README 明说公开可见≠可复用）                                                                                                                                                             |

### 1.2 能力矩阵

| 模块          | 状态              | 代码位置                                              | 说明                                                                                                          |
| ----------- | --------------- | ------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| 本地方案/志愿组编辑  | ✅ 可用            | `desktop_editor.py` / `plan_store.py`             | 上下移动=优先级，同组互斥，可存空方案                                                                                         |
| 只读余量监测      | ⚠️ 可跑但打不到真数据    | `client.py` / `scheduler.py`                      | HTML 表格解析器写得很扎实（GBK 严格解码、table_marker 唯一性、行级预筛），但 `protocol.verified=false` 时直接抛异常，而全仓没有一份真实的 `protocol` 段落 |
| EXE 内登录     | ⚠️ 协议已写，未实机验收   | `ncwu_signed_login.py` (287行) / `native_login.py` | 支持 `plain_20261103` 与 `signed_20261114` 两种形态，签名形态 fail-closed                                               |
| 学期接口        | ⚠️ 一次性只读探针      | `ncwu_term_probe.py`                              | 无 body POST `showYearTerm.action`，只校验 JSON 顶层结构                                                             |
| 本人课表        | ⚠️ 只读，参数不可复现    | `native_timetable.py`                             | `param/param2` 是动态/不透明值，代码主动拒绝静态回放                                                                          |
| 已选课程网页      | ⚠️ 被门禁挡住        | `known_school_pages.py`                           | `params` 查询串生成算法未还原                                                                                         |
| 自动选课编排      | ✅ 逻辑完整，**无法执行** | `enrollment.py` 452行 + `enrollment_flow.py` 262行  | FSM + HMAC 日志 + 进程锁 + 读回确认，工程质量其实高于多数商业脚本                                                                   |
| **写适配器**    | ❌ **不存在**       | —                                                 | 无 `submit_once` 实现，无提交端点，无幂等键                                                                               |
| **课程目录适配器** | ❌ **不存在**       | —                                                 | 只有一个 HTML 表格模板，且字段未验证                                                                                       |
| 时延优化层       | ❌ **写好了没接线**    | `tools/kingo-live/optimizations.py`               | ClockSync / Prewarmer / ReleaseMonitor / FailureClassifier / FailoverPlanner **五件套全部只服务于研究 CLI，主客户端一行没引用**  |
| 移动端接口       | ❌ 只做过结构统计       | `docs/acceptance/ncwu-xiqueer-har-2026-11-11.md`  | 53 个 POST 到 `api.xiqueer.com/manager//wap/wapController.jsp`，带 `token`/`xqerSign`/`encrptSecretKey`，签名算法未还原 |
| 通知          | ✅ 可用            | `scheduler.py`                                    | PushPlus / ServerChan，失败不消耗提醒额度                                                                             |

### 1.3 三个真正的硬堵点

按「卡住整条链的程度」排序：

**堵点 1：课程目录端点 + 余量字段语义（最致命）**  
目前只有一条 HTML 路径的**模板**，`columns: {course_id: 1, course_name: 2, teacher: 3, remain: 4}` 是猜的。已知的 JSON 里 `zxinfo[0].zxxq[]` 带 `kcmc/skbj/kcid/yixuan/kex`——`yixuan`（已选）和 `kex`（可选）看上去就是余量的两个分量，但**没和官方 UI 对过一次**，不能当成 `remain` 用。  
现在代码的做法是：`remain is None` → 跳过，`remain == 0` → 跳过。语义一错，整条链要么全跳要么全错。

**堵点 2：提交写合同（完全空白）**  
仓库里没有 `xkOper.jsp` / `addXk` / 任何提交端点的痕迹。这意味着：

- 不知道提交成功/失败的响应结构 → `FailureClassifier` 只能靠 `full_markers` 猜中文
- 不知道幂等语义 → HMAC 日志防重发的设计是对的，但防的是「自己的重发」，防不了「服务端已经收了但没回包」
- `SubmitReceipt.accepted` 三态（true/false/unknown）设计得很好，但没有任何东西能产生它

**堵点 3：选课窗口状态判据**  
已知判据只有一条：响应里 `prohibit: "1"` + `当前时间不是有效的选课时间区段!`。  
问题在于：**T0 那一瞬间，这个字段什么时候翻 0？** 是准点翻，还是服务端有个几十秒的预热期？这个只有实测才知道。现在的设计是「每次写之前查一次窗口」，60 秒轮询粒度完全抓不到亚秒级边界。

### 1.4 架构层面的自锁（必须点破）

`enrollment.py:_gate()` 要求 6 个条件同时为真：

```
user_opted_in                    ← 用户点勾选，能真
institution_permits_write        ← 构造函数默认 False，全仓无置真路径
gateway.official_catalog_verified ← 无实现
gateway.official_enrollment_verified ← 无实现
gateway.official_window_verified  ← 无实现
gateway.official_write_verified   ← 无实现（关键）
```

结论：**就算你现在有完整的接口，代码也跑不起来**。这已经背离 fail-closed 的初衷，成了 fail-locked。

正确形态不是把 bool 手改成 `True`（那等于删掉安全设计），而是把 bool 换成**带证据的 attestation 对象**：

```yaml
official_write:
  verified: true
  evidence: "docs/acceptance/xxx.md#L40"
  captured_at: "2026-12-21T10:03:22+08:00"
  contract_hash: "sha256:..."
  reviewer: self
  expires_at: "2027-03-01"   # 过期自动回落 false
```

这样既保留「没证据不许写」，又给了合法的置真通道。**这是 P0 改动，一周内可完成。**

---

## 2. 仓库自带的量化结论（还没被用起来）

`tools/bench/` 里两组数据非常关键，直接决定优化方向该往哪投。

### 2.1 p5 并发矩阵（seats=3 / background=12 / repeats=30）

| 策略               | 成功率     | 冲突数    |
| ---------------- | ------- | ------ |
| baseline（无预热无轮询） | 0.0     | 0      |
| high_freq_poll   | **1.0** | 0      |
| direct_api       | **1.0** | 0      |
| prebuild         | **1.0** | 0      |
| concurrent（8 并发） | 0.125   | 0      |
| concurrent + 乐观锁 | 0.125   | **63** |

### 2.2 时延场景矩阵（RTT 18ms / limiter 20rps / 队列 4 worker）

| 场景                                                             | 成功率     | 请求数 | 发现 p50       | 提交 p50     | 总 p95     |
| -------------------------------------------------------------- | ------- | --- | ------------ | ---------- | --------- |
| baseline                                                       | 1.0     | 1   | **28118 ms** | 20.2 ms    | 70 ms     |
| high_freq_poll                                                 | 1.0     | 1   | 280.9 ms     | 20.2 ms    | 70 ms     |
| prebuild                                                       | 1.0     | 1   | 280.9 ms     | **9.2 ms** | **59 ms** |
| direct_api                                                     | 1.0     | 1   | 280.9 ms     | 10.2 ms    | 60 ms     |
| concurrent                                                     | 0.125   | 8   | 280.9 ms     | 87.1 ms    | 1178 ms   |
| multi_session                                                  | 0.15    | 20  | 284.2 ms     | 1042.7 ms  | 1145 ms   |
| proxy_pool                                                     | 0.15    | 20  | 284.2 ms     | 1042.7 ms  | 1145 ms   |
| pulse_burst                                                    | 0.013   | 80  | 280.9 ms     | 1369.0 ms  | 1481 ms   |
| race_repeat                                                    | 0.062   | 16  | 280.9 ms     | 1044.0 ms  | 1126 ms   |
| ignore_limit                                                   | 0.125   | 8   | 280.9 ms     | 25.5 ms    | 280 ms    |
| captcha_bypass / tamper_params / queue_skip / endpoint_exploit | **0.0** | 1   | —            | —          | —         |

**三条读出来的硬结论：**

1. **发现时延是主战场。** baseline 到 high_freq_poll，发现时延从 28 秒降到 281 毫秒，**100 倍差距**；提交时延只有 20ms，占总量不到 3%。优化提交速度的边际收益 ≈ 0。
2. **并发是负收益，而且是灾难性的。** 8 并发成功率从 100% 掉到 12.5%，总 p95 从 70ms 涨到 1178ms。20 会话/代理池掉到 15%，80 次脉冲爆发掉到 1.3%。青果的选课是数据库行锁 + 服务端队列，**并发只会把自己排到队尾**。
3. **歪路全灭。** 绕验证码、改参数、跳队列、打端点漏洞，模拟里成功率 0.0——不是「不该做」，是**这条路本身不通**。

**但当前的代码方向是反的**：`enrollment.py` 里写 `Writes are serial`（串行）——这点对了；可 `RequestPolicy.max_concurrency` 默认还是留了口子，而时延优化层压根没接进主流程。等于**结论正确，执行没跟上**。

---

## 3. 优化方向（按 ROI 排序）

### P0-1 门禁改造：bool → attestation（解锁全部后续工作）

- 位置：`core/enrollment.py:_gate()`、`core/native_preparation.py`、`config.example.yaml`
- 做法：新增 `core/attestation.py`，定义 `Attestation(verified, evidence_path, contract_hash, captured_at, expires_at)`；`_gate` 改为校验 attestation 且 `captured_at` 在有效期内
- 验收：`pytest tests/test_enrollment.py` 全绿 + 新增 3 条用例（无证据拒绝 / 过期拒绝 / 有效通过）
- 收益：从「永远跑不了」变成「有证据就能跑」
- 工作量：1-2 天

### P0-2 时延埋点：先量再优

- 位置：`core/client.py:_budgeted_request`
- 埋点字段：`dns_ms / tcp_ms / tls_ms / ttfb_ms / download_ms / parse_ms`（httpx 可通过 `response.extensions["network_stream"]` 拿，或用 `httpcore` trace）
- 输出：每次监测写一行 JSONL 到 `data/latency.jsonl`，GUI「工具与日志」加一个「时延分布」页签显示 p50/p95
- 验收：能看到「单次轮询 = 多少 ms，花在哪一段」的分解
- 收益：没有这个，后面所有优化都是盲调
- 工作量：1 天

### P0-3 合并两套代码，把 optimizations 接进主流程

现在 `tools/kingo-live/` 和 `clients/windows/` 是两套 HTTP 客户端、两套解析器、两套配置格式。`optimizations.py` 里五个组件质量都不错，但主客户端一个都没用。

- `ClockSync` → 接 `scheduler.py`，用于 T0 对齐
- `Prewarmer` → 接监测启动前，提前建连 + 验会话
- `ReleaseMonitor` → 接 `scheduler.py` 的轮询循环，做快照差集
- `FailureClassifier` → 接写适配器的响应分类
- `FailoverPlanner` → 与 `enrollment_flow.py` 的 group 语义合并（现在两者逻辑重复但细节不一致）
- 验收：删除 `tools/kingo-live/live_client.py` 中的重复传输层，改为 `import core.client`
- 工作量：3-4 天

### P1-1 单次轮询压到 150ms 以内

现状 `discover_p50 = 281ms`（模拟值）。压法（按收益排序）：

| 手段                                                                       | 预期收益            | 依据                 |
| ------------------------------------------------------------------------ | --------------- | ------------------ |
| 只查目标课（`course_ids` 字节预筛，已实现）                                             | 减少 60-90% 解析量   | `client.py:202` 已有 |
| 跳过 BeautifulSoup 全解析，改为**字节级 remain 定位**：预编译正则直接抽第 4 列的 `<td>` 内容        | 解析 15ms → 1ms   | 解析占比在 400 行表格上很可观  |
| Body 预编码（`encode_gbk_form` 已实现，缓存在 `_compiled_query`）                    | 省 1-3ms/次       | 已有                 |
| `keepalive_expiry` 从 60s 提到 300s，避免窗口期重握手                                | 省 TLS 100-300ms | `client.py:247`    |
| 关闭 `lxml` 的 DTD/实体处理（`BeautifulSoup(..., "lxml", parse_only=...)` 或直接弃用） | 省 3-5ms         | —                  |
| 用 `asyncio` + `uvloop`（Windows 上不行，跳过）                                   | —               | Windows 无 uvloop   |

- 目标：`discover_p50 < 150ms`，`total_p95 < 250ms`
- 验收：本地对同一份 4MB 级 HTML fixture 跑 100 次，取 p50

### P1-2 三未知的取证路线（不需要等窗口也能推进 80%）

| 未知     | 现在能做的                                                                     | 需要窗口才能做的            |
| ------ | ------------------------------------------------------------------------- | ------------------- |
| 目录端点结构 | 从 HAR 里 `zxinfo/zxxq` 反推字段语义；用 `har_to_schema.py`（807 行，已经很强）生成 schema 草案 | 官方 UI 对照确认 `remain` |
| 余量语义   | 找「容量」字段（可能是 `kex` 或响应另一层）+ 「已选」字段 `yixuan`，验证 `remain = cap - yixuan`     | 同一时刻 UI 与接口对拍       |
| 写合同    | 从 53 个移动端 POST 里按 URL 聚类，找出含 `kcid`+`skbj` 的那一簇，反推提交动作                    | 一次真实提交的完整请求+响应      |

**这条是整份方案里唯一「必须老板配合」的部分**——见第 6 节。

### P2-1 GUI 拆分

`windows_gui.py` 1850 行单文件，已出现 `_xxx_in_progress` 布尔标志 11 个（`_entry_probe_in_progress` / `_session_check_in_progress` / `_term_probe_in_progress` ...）。这是典型的「互斥靠布尔堆」反模式，加第 12 个功能必然漏。

- 拆为 `ui/home_tab.py` / `ui/manual_tab.py` / `ui/tools_tab.py` / `ui/state.py`
- 11 个布尔合并成一个 `OperationLock` 上下文管理器
- 工作量：2-3 天，纯重构，无功能变化

### P2-2 观测与复盘

- 每次运行输出 `data/run-<ts>.json`：目标、每阶段时延、窗口状态时间线、提交结果、读回结果
- GUI 加「上次运行复盘」：把时间线画成横向条形图，一眼看出卡在哪
- 收益：抢课失败时能定位是「没发现」还是「发现晚了」还是「提交被拒」

---

## 4. 特殊抢课方向（重点）

这一节是「除了把主路径做通，还有哪些别人没想到的角度」。按投入产出比排序。

### 4.1 移动端 API 旁路（价值最高）

**事实**：2026-11-11 HAR 里 `api.xiqueer.com/manager//wap/wapController.jsp` 出现 **53 次 POST**，请求体带 `token`、`xqerSign`、`encrptSecretKey`、`param`、`param2`。响应里直接给结构化 `zxinfo[0].zxxq[]`（含 `kcmc/skbj/kcid/yixuan/kex`）和 `xksj`（选课时间）。

**为什么这条线优于 Web 端：**

- 移动端**不做 HTML 渲染**，直接吐 JSON → 解析成本从 15ms 降到 0.5ms
- 移动端一般**不校验 Referer、不校验 UA 与 IP 一致性**
- 移动端接口通常是**同一个后端**，但走的是 app 专用 controller，**风控策略往往比 Web 薄**
- `zxxq` 里已经带了 `yixuan`（已选）和 `kex`（可选）——**余量语义可能在这里直接就有的，不用去猜 HTML 表格列**

**要做的三件事：**

1. **还原 `xqerSign` 生成算法**——拿喜鹊儿 APK 反编译（`apktool` + `jadx`），搜 `xqerSign` / `encrptSecretKey` 字符串，定位到 Java 层或 native 层（大概率在 `libnative-lib.so` 里，需要用 IDA 看 JNI）
2. **还原 `param/param2` 的编码**（Base64+DES？还是 gzip+自定义？）——用同一份 HAR 里多组已知明文/密文对拍验证
3. **确认服务端是否与 Web 端共用选课池**——提交一门前端已满的课，看两边 remain 是否同步

**提交动作的定位方法**：把 53 个 POST 按 `param` 里的关键词聚类（`kcid` + `skbj` 同时出现的那一簇就是选课动作），然后看 URL 上的 `method=` 参数或 `param` 里的 `action=` 字段。

**收益预估**：如果打通，整个监测链的端到端时延可从 ~300ms 降到 ~120ms，且拿到结构化余量字段，**堵点 1 直接消失**。

### 4.2 捡漏（ReleaseMonitor 的放大版）

`optimizations.py:ReleaseMonitor` 已经实现了 `new / reopened / increased` 三态检测，但只针对「目标课」。放大做法：

- **全池快照**：一次请求拿整个池（不只目标课），存 `fingerprint -> remain` 字典
- **退课高峰建模**：青果系统的退课高峰有三个确定窗口：
  - 选课首日 T0+30 分钟（抢错了立刻退）
  - 选课截止前 2 小时（犹豫的人集中退）
  - 补选期首日 08:00（前一轮被清退的名额释放）
- **策略**：高峰窗口把轮询从 60s 提到 30s（`ABSOLUTE_MIN_INTERVAL` 硬底），非高峰回 60s。**不要提到 30s 以下**——模拟数据显示 `ignore_limit` 成功率 12.5%，得不偿失
- **判定**：`prev <= 0 and cur > 0` 就是坑，立刻单发提交，不等下一个周期

**关键细节**：捡漏场景下「提交被拒」的概率很高（别人比你快），所以 `enrollment_flow.py:248` 的 `explicit_rejection → reads=1 → next_preference` 这条路径必须保证快——现在是 1 次读回，正确；但 `VerificationPolicy.interval_seconds` 默认 60 秒，捡漏时应该降到 3-5 秒。

### 4.3 窗口亚秒边界（T0 战役）

`ClockSync` 已经能算 offset，`wait_until` 已有 `sleep 1s 切片 + 最后 20ms 忙等`。缺的是**服务端时钟精度**：


- `Date` 头只有 1 秒分辨率 → `precision_ms = 1000`，等于没对齐
- 需要找毫秒级来源：`showYearTerm.action` 的响应体里有没有时间字段？响应头 `X-Server-Time`？还是用「一个已知长度的 GET，用 TTFB 反推」？
- 建议做法：**用 prohibit 翻转时刻校准**。T0 前 3 秒开始 200ms 间隔探测，记录「第一次 prohibit 变 0」的本地时间戳，与公告时间比对，得到真实 offset。这个 offset 存下来给下一次用

- `lead_ms` 建议 **120–180ms**（依据：sim 里 `submit_p50 = 20ms`，RTT 18ms，服务端排队 ~80ms）——**不要提前太多**，早了服务端还返回 prohibit=1，白耗一次请求；晚了坑就没了

### 4.4 分批开放的时间差

华水按年级/学院分批开放（`2026-12-21~25`，精确时段看后续通知）。可做的事：

- 高年级时段**只观察不提交**，记录该课程的 remain 曲线：什么时候从 N 掉到 0、掉到 0 之后有没有回弹
- 用这条曲线预测低年级时段的「安全起跳时刻」——如果高年级是 T0+8 秒打完，低年级大概率类似
- 严格约束：**不伪造身份、不跨批次提交**。只做观察与预测

### 4.5 多教学班策略：串行 + 快速放弃

同课程 3-5 个教学班时的正确顺序：

```
班1 提交 → 读回(1次) → 成功:结束
                    → 明确拒绝:立即班2（刷新目录，不等）
                    → 结果未知:停，不许重发
```

代码里这套逻辑**已经写对了**（`enrollment_flow.py:233-260`）。缺的是把 `refresh_catalog` 的成本压低——现在拒绝后要重新拉一次全量目录，这一拉可能就是 300ms。优化：**只重拉目标课的 3 个班**（`course_ids` 过滤已支持）。

**不要做的事**：同时提交 3 个班，成功了再退 2 个。理由：p5 数据显示并发冲突 63 次、成功率 12.5%；且「选了再退」会占用池子，触发其他同学的名额锁定，实际是负和。

### 4.6 浏览器 CDP 兜底（不是主路径）

**适用场景**：`params` 这类加密查询串还原不出来、或者服务端加了指纹风控时。

- 方案：Playwright 驱动真实 Chrome，`--remote-debugging-port` 连接已登录的实例
- 优势：JS 环境完整，加密参数由页面 JS 自己算，不用逆；UA/指纹/Canvas 全真
- 劣势：单次操作 300-800ms，比 HTTP 慢一个数量级
- 正确用法：**预热阶段**用 CDP 打开选课页、执行一次真实查询、把生成的 `params` 和会话 cookie 取出来，**然后切回 HTTP 单发**。这样既有真实参数，又有 HTTP 的速度

这是「特殊方向」里唯一能绕过加密参数逆向的方案，工程量小，建议作为 P1 优先级。

### 4.7 明确不做清单（有数据支撑）

| 手段 | 模拟成功率 | 结论 |
|---|---|---|
| 8 并发提交 | 0.125 | 不要 |
| 20 会话 / 代理池 | 0.15 | 不要 |
| 80 次脉冲爆发 | 0.013 | 不要，纯送人头 |
| 重复竞争提交 | 0.062 | 不要 |
| 忽略限流 | 0.125 | 不要，触发封禁 |
| 绕验证码 | 0.0 | 不通 |
| 篡改参数 | 0.0 | 不通 |
| 跳队列 | 0.0 | 不通 |

这份清单应该直接写进 README 的「不要做」章节，省得后来者重复踩。

---

## 5. 落地路线图

| 阶段 | 内容 | 交付物 | 验收判据 | 依赖 |
|---|---|---|---|---|
| **A（1 周）** | 门禁 attestation 化 + 时延埋点 + 合并 optimizations | `core/attestation.py`、`data/latency.jsonl`、删除重复传输层 | 单测全绿；能看到一次轮询的六段分解 | 无 |
| **B（1 周）** | 三未知取证（离线部分）+ ReleaseMonitor 接入 + 亚秒窗口状态机 | schema 草案、退课高峰轮询策略、`WindowProbe` | 用 HAR 生成的 schema 能过 `har_to_schema` 校验；窗口状态机能识别 prohibit 翻转 | 需 HAR/APK |
| **C（1-2 周）** | 写适配器（先接移动端，后接 Web）+ 幂等 + 读回 | `core/adapter_ncwu.py` | 补选期低风险窗口完成 1 次真实提交 + 读回确认 | 需真实窗口 |
| **D（1 周）** | CDP 兜底 + GUI 拆分 + 复盘页 | `ui/` 拆分、`run-<ts>.json` 时间线 | 复盘页能定位失败原因 | A/B 完成 |

**关键判断**：A 阶段全部不需要任何外部输入，**现在就能开工**。C 阶段是真正的分水岭，之前所有工作都只是给它铺路。

---

## 6. 需要老板拍板 / 提供的

按「卡住进度」的严重程度排序：

1. **一次完整的选课窗口 HAR，必须包含一次真实的「提交成功」和一次「课程已满」**
   → 这一份东西能同时解掉堵点 1、2、3。没有它，C 阶段永远开不了工。
   → 抓法：F12 → Network → 勾 `Preserve log` → 从进入选课页点到提交成功 → 右键 `Save all as HAR with content`
   → 脱敏后放 `docs/acceptance/`（现在仓库里一份原始 HAR 都没有，只有描述）

2. **喜鹊儿 APK**（官网或应用商店下载，`com.kingosoft.*` 包名）
   → 用于还原 `xqerSign`。这是 4.1 那条高价值路线的前提。

3. **主目标是什么课**：公选课 / 体育 / 专业课 / 重修？
   → 池子、开放批次、余量刷新频率完全不同。公选课最卷（几百人抢几十个坑），体育课按项目分池，专业课基本不抢。
   → 这决定 4.2 的轮询策略参数，也决定要不要做 4.4。

4. **能不能接受走移动端接口**（技术上更优，但要确认服务端是否同池、是否有额外的 app 侧校验）

---

## 7. 风险（简短）

| 风险 | 触发条件 | 后果 |
|---|---|---|
| 账号被风控 | 轮询低于 30s、并发、脉冲 | 临时封禁 / 强制改密 |
| 学分状态脏 | 提交结果未知时重发 | 重复选课 / 名额被锁 |
| 学校通报 | 高频访问被 WAF 记录 | 教务约谈 |

现有代码对这三条**已经做了相当扎实的防护**（`ABSOLUTE_MIN_INTERVAL=30`、HMAC 日志防重发、unknown 即停）。本方案不建议放松任何一条。

---

## 8. 附：本方案的取舍说明

- 没有提议重写任何已工作的模块。`enrollment.py` / `enrollment_flow.py` 的设计质量高于同类项目，动它就是浪费。
- 没有提议加更多单测。99 项已经够了，**现在缺的是真实数据，不是测试覆盖**。
- 没有提议做 Android。`clients/android/` 只有 README，在 Web 端写链没打通之前做 Android 是重复劳动。
- 唯一主张「大改」的地方是 §1.4 的门禁自锁——这是当前项目最大的结构性问题。
