# kingo-live — 真实 HTTP 选课客户端 + 高收益策略层

**这是真实客户端，不是仿真。** `live_client.py` 里没有一处模拟逻辑：每个请求都通过
`http.client` 发起真实 TCP/TLS 连接，会话 Cookie 来自环境变量，代理走真实 CONNECT
隧道，请求预算用 SQLite 事务 + 跨进程 OS 文件锁，提交是真实 POST，成功判定只认
官方已选名单的真实回读。

`selftest.py` / `test_optimizations.py` / `cli_smoke.py` 里的回环 HTTP 服务只是
**验证用的靶标**：因为当前没有配置真实站点和会话，用它确认客户端真的能发请求、
真的能解析、真的能提交、真的能回读。把 `config.json` 里的 `base_url` 换成目标站点、
填好接口路径和字段映射，同一份代码直接打真实系统。

## 文件

| 文件 | 作用 |
|---|---|
| `live_client.py` | 传输层、预算、意图日志、CLI 与链路编排。零第三方依赖，Python 3.10+ |
| `optimizations.py` | 策略层：`ClockSync` / `Prewarmer` / `ReleaseMonitor` / `FailureClassifier` / `FailoverPlanner` |
| `config.example.json` | 配置模板，含全部待定位占位符 |
| `selftest.py` | 8 项真实 HTTP 端到端回归测试 |
| `test_optimizations.py` | 13 项真实 HTTP/1.1 策略层测试 |
| `cli_smoke.py` | 10 条真实 CLI 子进程冒烟 |
| `har_to_schema.py` | **新增** HAR → config 字段映射：三接口识别 / 列名推断 / 业务文案 / 时钟头 / 凭据脱敏 |
| `capture.example.har` | **新增** 合成示例 HAR，用来立刻跑通下面三条命令（非真实数据） |
| `../bench/academic_latency_sim.py` | 离线仿真，含名额分配规则与锁策略的 P5 敏感度矩阵 |

## 跑起来

```bash
cd kingo-live
python selftest.py -v              # 8/8  OK
python test_optimizations.py -v    # 13/13 OK
python cli_smoke.py                # 10/10 PASS
python har_to_schema.py --self-test   # 16/16 checks passed
cd ../bench
python academic_latency_sim.py --self-test     # 8/8 checks passed
python academic_latency_sim.py --p5-matrix --json p5-results.json
```

指向真实系统：

```bash
cp config.example.json config.json     # 按下面表格填占位符
export KINGO_COOKIE='...'              # Windows: set KINGO_COOKIE=...
python live_client.py --config config.json --probe
python live_client.py --config config.json --sync-clock
python live_client.py --config config.json --warmup
python live_client.py --config config.json --watch-events 20
python live_client.py --config config.json --enroll --confirm
python live_client.py --config config.json --reconcile
python live_client.py --config config.json --measure
python live_client.py --config config.json --auto --confirm   # 全链路
```

`--enroll` 与 `--auto` 的提交阶段都必须显式带 `--confirm`；不带时 `--auto` 只跑
只读阶段（时钟对齐 / 预热 / 事件监控），不发任何提交请求。`--set` 可覆盖任一策略键：

```bash
python live_client.py --config config.json --probe --set direct_api=true
python live_client.py --config config.json --monitor 10 \
  --set poll_interval_ms=500 --set proxies=http://10.0.0.2:8080,http://10.0.0.3:8080
python live_client.py --config config.json --enroll --confirm --set prebuild=true
```

## 取真实 schema：三条路径（当前唯一缺口）

客户端、策略层、仿真都已跑通，缺的只有**真实接口契约**：目录 / 提交 / 已选名单三个
端点的路径、字段名、业务失败文案和时钟响应头。三条取数路径，任选一条。

### 路径 A（最省事）：导出 HAR，脚本自动出 config 补丁

1. Chrome / Edge 打开教务选课页并登录 → `F12` → **Network**。
2. 勾上 **Preserve log（保留日志）**，过滤器选 **Fetch/XHR**。
3. 完整走一遍：进入选课目录页 → 查询一次课程列表 → 打开「已选课程」页 →
   （窗口开放时）手动提交一门课。**这三步产生三条关键请求。**
4. Network 列表里右键 → **Save all as HAR (with content)**，存成
   `kingo-live/capture.har`（Firefox：右键 → 复制全部为 HAR，再粘进文本文件）。
   **给本地文件路径，不要把 HAR 内容贴进聊天**——里面带会话 Cookie。

```bash
python har_to_schema.py capture.har --list          # 先看候选表与打分
python har_to_schema.py capture.har --merge config.json
# 产物：schema-draft.json / har-report.json / config.merged.json
python har_to_schema.py capture.har --pick catalog=12 submit=27 enrollment=31 --merge config.json
python har_to_schema.py capture.har --strict          # 连课程号一起脱敏
python har_to_schema.py --self-test                   # 16/16 自测
```

没有真实 HAR 时，先拿合成样例跑一遍看输出形态：

```bash
python har_to_schema.py capture.example.har --list
python har_to_schema.py capture.example.har --merge config.example.json \
  --out example-schema-draft.json --report example-har-report.json \
  --out-merged config.example.merged.json
```

脚本实际做的事：

| 动作 | 输出 |
|---|---|
| 过滤 `.js/.css/.png` 等静态资源，自动锁定 API host | `har-report.json` 候选表 |
| 按响应行结构 + URL 关键词 + POST 表单给三个角色打分 | `picks.catalog/submit/enrollment` |
| 推 `json_list_path`（如 `data.rows`）与 `columns.*` | `catalog` / `enrollment` 块 |
| 从 `postData` 生成 `submit.fields`：`kcid→{course_id}`、`skbj→{class_id}`、`xnxq→{xnxq}`、票据→`{token}` | `submit` 块 |
| 抓「已满 / 不是有效的选课时间」等文案 | `business.full_markers` / `window_markers` |
| 找 `Date` 或毫秒级自定义时间头，标注精度；把 `Server-Timing` 单独归为延迟头（**不能当时钟解析**） | `clock.header` / `precision_ms` / `stage_split_headers` |
| 解码服务端返回的 `\uXXXX` 转义 JSON，保证中文业务文案能被识别 | 同上 `business.*` |
| Cookie / Authorization / token / 签名值替换为 `<REDACTED:len=N>` | 可外发的报告 |
| **把课表接口排除在 catalog 之外** | `NOT_CATALOG:timetable` 警告 |

最后一条直接对应 `docs/acceptance/ncwu-xiqueer-har-2026-11-11.md` 的结论：
`/hsjw//wap/mycourseschedule.action` 返回的是课表（`xn/xq/zc/week1..week7/sjhjinfo`），
**不是可选课程目录**；`prohibit:"1"` + 「当前时间不是有效的选课时间区段!」只证明
窗口没开，不能当目录字段。脚本命中这类响应会打 `window_closed_evidence` 并清零
catalog 分数。

### 路径 B：只跑一次 `--probe`

只需先填 `base_url` 和一个候选 `catalog.path`：

```bash
set KINGO_COOKIE=<登录后 F12 任一请求的 Cookie 头>
python live_client.py --config config.json --probe \
  --probe-bytes 4096 --probe-save probe-body.txt
```

发一次真实 GET，打印 body 预览、可用于时钟对齐的响应头、`parsed=N`，并把**完整**
body 落到 `probe-body.txt`。把这个文件路径给我即可。`--probe-bytes` 控制预览长度，
默认 512 字节常常不够看完整 JSON 层级。

### 路径 C：手工三段（信息量最小）

存成 `schema-notes.txt`，每行照抄 F12 → Network → 对应请求的 **Headers / Form Data /
Response**（Response 只贴第一行完整记录就够）：

```text
[catalog]    GET  https://HOST/PATH?xnxq=2026-2027-1
             {"data":{"rows":[{"kcid":"...","skbj":"...","kyl":3,...}]}}
[submit]     POST https://HOST/PATH
             kcid=...&skbj=...&xnxq=...&token=<删掉>
             {"ok":true,"msg":"选课成功"}
[enrollment] GET  https://HOST/PATH
             {"data":{"rows":[{"kcid":"...","skbj":"..."}]}}
[headers]    Date: Fri, 13 Nov 2026 02:48:00 GMT
             Server-Timing: queue;dur=8.5, db;dur=22.1   (有就贴)
```

### 边界与验证判据

- **窗口未开放时**目录接口可能只返回 `prohibit` 文案，抓不到真实列；至少把
  「已选名单」响应和响应头拿到，目录等窗口内再抓一次。
- **提交接口只有在窗口内手动点过一次才会出现在 HAR 里**。窗口外没有这条记录时，
  脚本会输出 `no submit candidate found -> DO NOT guess a write endpoint`，
  不要拿目录页的任意 POST 去填 `submit.path`。
- **凭据不要贴聊天**：Cookie / token / Authorization / 学号 / 姓名。HAR 报告默认
  已脱敏，仍建议只给本地文件路径；`--strict` 连业务值一起脱敏。
- 字段映射填完的判据：`--probe` 返回 200 且 `parsed=N` 与官方 UI 行数一致；
  `--monitor` 的 `seat=N` 与官方余量一致；`--enroll --confirm` 后官方已选名单
  出现精确 course+class 行。

拿到任意一条路径的产物，我这边接的活：对齐 `catalog.columns` / `submit.fields` /
`business.*` / `clock.header`，然后跑真实 `--probe → --sync-clock → --warmup →
--watch-events → --auto --confirm` 全链，用官方回读命中率做验收。

## 策略层：为什么这五项才是真正抬成功率的

上一轮分析把成功率拆成六道门，客户端只能动 P1/P2/P4/P6，P5 由服务端分配规则决定。
本轮把可控的四道门全部做成真实模块：

| 门 | 模块 | 真实动作 |
|---|---|---|
| P1 窗口有效 | `ClockSync` | 从真实响应头采样服务器时钟，N 次取中位数，`RTT/2` 中点补偿；`wait_until()` 阻塞到目标服务器时间前 `lead_ms` |
| P1 身份有效 | `Prewarmer` | 窗口前真实探测登录态、完成 DNS/TCP/TLS 握手；`keep_alive` 下复用真实持久连接 |
| P2 名额存在 | `ReleaseMonitor` | 对连续真实目录读做快照差分，只在 `new` / `reopened` / `increased` 时产出事件，空轮询不触发提交 |
| P2 目标正确 | `FailoverPlanner` | `course_id` + `class_id` 精确匹配，按 `priority` 排序，失败切下一个教学班，**不重试当前目标** |
| P4/P6 结果可信 | `FailureClassifier` + `Journal` | 每个真实结果映射成唯一动作；未知结果写 `pending` 并停机，`--reconcile` 用官方名单对账 |

### 时钟精度的硬事实

RFC 7231 的 `Date` 头**只有 1 秒分辨率**，`ClockSync.precision_ms` 如实标 `1000.0`，
`--sync-clock` 输出的 `spread_ms` 是样本离散度。若部署暴露毫秒头（如
`X-Server-Time: 1799808000123`，epoch 毫秒或 ISO-8601），把 `clock.header` 改成它，
`precision_ms` 变 `1.0`。不编造不存在的精度。

`clock.window_epoch` 是**服务器时间的 Unix 秒**（不是本地时间）。先用 `--sync-clock`
拿到 `now_server_epoch` 与 `offset_ms`，再用公告的开放时刻换算；`0` 表示不做定时调度。

### 失败决策表

| kind | action | 客户端行为 |
|---|---|---|
| `auth` (401/403) | `halt` | 停止链路，重新登录 |
| `rate_limited` (429) | `cooldown` | 停止链路，按 Retry-After 冷却，不重发 |
| `overload` (503/502/504) | `backoff` | 睡 `business.backoff_ms` 后切下一个目标 |
| `window_closed` | `wait` | 停止链路，等窗口重开再调度 |
| `business_full` / `business_rejected` | `next` | 该教学班已死，切下一个备选 |
| `no_match` / `seat_unavailable` | `next` | 目录里没有精确目标或余量为 0，切下一个 |
| `confirmed` | `stop` | 官方名单回读命中，链路结束 |
| `pending` / `unknown` | `halt` | 结果未知：写 `pending` 意图并停机，等 `--reconcile`，**绝不自动重试** |

`--reconcile` 不存明文课程号：读官方名单每一行，用同一 HMAC 盐重算指纹，与 journal
里的 pending 指纹比对，命中写 `confirmed`、未命中写 `rejected`。实测两条路径都覆盖：
提交实际成功但回读 503 → 对账后 `confirmed`；提交未落地 → 对账后 `rejected`。

## 占位符：怎么定位 / 已知常量 / 怎么确认填对了

| 占位符 | 定位方法 | 已知常量 | 验证判据 |
|---|---|---|---|
| `base_url` | 官方选课通知里的教务直链；F12 → Network → Doc 的域名 | 校内教务域，HTTPS | `--probe` 返回 200 且 body 是课程数据、不是登录页 |
| `catalog.path` | F12 → Network → XHR/Fetch，找返回课程数组的那个 Request URL | 通常带 `xnxq` 学期参数 | `--probe` 的 body preview 出现课程号字段 |
| `catalog.format` / `json_list_path` | 看响应 Content-Type 与 JSON 层级（如 `data.rows`） | `application/json` → json；`text/html` → html | `parsed=N` 中 N 与官方 UI 行数一致 |
| `catalog.columns.*` | JSON 用响应 key 名；HTML 用表头顺序（**0-based**） | JSON 字段名 / HTML 列索引 | `--probe` 的 `parsed=N` 且 `--monitor` 能打印 `seat=...` |
| `columns.remain` | 官方 UI 的「余量」列，与「容量」「已选」列对照，别拿错 | 余量可被 `parse_remain()` 从「余 3」「3/60」里抽数字 | 手工改一门课余量，`--monitor` 输出 `seat=N` 与 UI 一致 |
| `submit.path` | 在官方 UI 手动提交一次，抓 XHR 的 Request URL | POST | `--enroll --confirm` 后官方已选名单出现该课程 |
| `submit.fields` | 照抄 Form Data 全字段；课程号 `{course_id}`、教学班 `{class_id}`、学期 `{xnxq}`、票据 `{token}` | 表单或 JSON 二选一，配 `encoding` | 服务端不返回参数错误/票据失效 |
| `enrollment.path` | 官方「已选课程」页抓 XHR | GET | 提交前空、提交后出现精确 course+class 行 |
| `KINGO_COOKIE` | 登录后 F12 任一请求的 Cookie 头 | 环境变量注入，**不写入 config** | `--probe` 返回已登录数据而非登录跳转 |
| `token` | 页面隐藏域 / 一次性票据 | 每次会话变化，短时有效 | 提交返回 200 而非 token 失效 |
| `clock.header` | 看响应头有无毫秒级时间头；没有就用 `Date` | `Date` 秒精度 / 自定义头毫秒精度 | `--sync-clock` 的 `precision_ms` 与头一致、`spread_ms` 收敛 |
| `clock.window_epoch` | 官方公告的开放时刻，按 `--sync-clock` 的 offset 换算 | Unix 秒，服务器时区 | `--auto` 的 `align.aligned=true` 且 `error_ms` 在 `precision_ms` 内 |
| `warmup.path` | 一个能区分「已登录 / 未登录」的轻量 GET（会话探测页或目录页） | GET，200 | `--warmup` 的 `all_auth_ok=true`；人为让会话过期应变 `false` |
| `business.success_field` | 抓一次真实提交响应，看成功标志字段名 | 如 `ok` / `success` / `code` | 已满的提交被判 `business_full`/`business_rejected` 并切下一个目标 |
| `business.full_markers` | 业务失败文案（「名额已满」「无余量」等） | 大小写不敏感子串 | 同上 |
| `targets[].class_id` | 官方目录里的教学班号；留空表示只按课程号匹配 | 精确字符串 | 填错时 `FailoverPlanner.match` 返回 `None`，不误提交到别的班 |
| `targets[].priority` | 主观优先级，数字小先试 | 1..N | `planner.history` 的顺序与 priority 一致 |

多会话用 `KINGO_COOKIE_1` … `KINGO_COOKIE_N`，配合 `--set sessions=N`。

## 数值

| 参数 | 主值（范围） | 依据 |
|---|---|---|
| `poll_interval_ms` | 60000（500–60000） | `config.example.yaml` 的 60s 客户端下限 |
| `clock.samples` | 5（3–9） | 中位数抗单次抖动；样本再多对秒精度头无收益 |
| `clock.lead_ms` | 50（10–200） | 覆盖一次 TLS 握手 + 目录读的准备余量 |
| `clock.max_wait_s` | 600（60–3600） | 单次调度上限，超时返回 `aligned=false` |
| `warmup.probes` | 2（1–4） | 第 1 次建连、第 2 次验证连接复用 |
| `business.backoff_ms` | 200（50–2000） | 503 退避，避免叠加到排队上 |
| `budget.rate` | 20 req/s（10–60） | 本地占位值，须换成核实过的请求上限 |
| `budget.burst` | 5（1–20） | 令牌桶瞬时容量 |
| `budget.retry_after_ms` | 1000（500–5000） | 429 后的保守冷却 |
| `timeout` | 5.0 s（1–15） | 校园网 RTT 18 ms × 约 200 倍余量 |
| `concurrency` | 1（1–16） | 默认串行；实测并发 8 把成功率 1.00 → 0.12 |
| `sessions` | 1（1–8） | 独立 Cookie 数 |
| `cycles` | 1（1–1000） | 监测轮数 |

## 策略开关 → 真实生效位置

| 开关 | 代码里真正改了什么 |
|---|---|
| `poll_interval_ms` | `Engine.monitor()` / `watch_events()` 的真实 sleep 间隔 |
| `concurrency` | `ThreadPoolExecutor` 的真实并发 POST |
| `burst` / `race_repeat` | 真实请求份数 `concurrency × race_repeat × burst` |
| `direct_api` | 目录读改走 JSON 通道，真实加 `Accept: application/json` + `X-Requested-With` |
| `sessions` | 建 N 个独立 Transport，各带自己的 Cookie |
| `proxies` | `HTTPSConnection.set_tunnel()` 真实 CONNECT / HTTP 绝对 URI |
| `respect_retry_after` | 为 false 时跳过冷却，**真的把请求发出去** |
| `prebuild` | 在计时窗口外预序列化 body，`client_ms` 里不再计入 |
| `warmup.keep_alive` | `Transport._open()` 缓存真实 `HTTPConnection`，`RawResponse.reused` 标记复用 |
| `verify_tls` | `ssl` context 的校验开关 |

## 四段延迟在真实环境怎么分离

`Transport.request()` 用 `perf_counter()` 在真实连接上打点：

```
client_ms              header/body 组装（含未预构造时的序列化耗时）
connect_ms             DNS + TCP + TLS / CONNECT 隧道；keep-alive 复用时为 0
ttfb_ms                send → response headers
network_ms             ttfb_ms − Server-Timing 已声明的服务端耗时
queue_ms / tx_ms       解析 Server-Timing: queue;dur=…, db;dur=…
server_unresolved_ms   后端不发 Server-Timing 时，两段无法从客户端拆开，如实留在此字段
```

**真实系统里服务端排队和数据库事务无法靠客户端直接拆开**，只有后端暴露
`Server-Timing`（或自建带埋点的反向代理）才能分离；没有时本客户端明确标
`unresolved`，不编造数字。这是真实测量与仿真最大的区别。

三种速度：

```
discover_ms  监测开始 → 目录读解析出 remain > 0
submit_ms    决定提交 → 提交响应头
confirm_ms   提交 → 官方已选名单里精确命中 course_id + class_id
```

传输层 200 只写 `pending` 意图，**只有名单命中才写 `confirmed`**。名单读成功但目标
缺失写 `rejected`（明确业务失败）；名单读本身失败保持 `pending`（结果未知），停机等
`--reconcile`。意图日志只存 HMAC 指纹，不存 Cookie、课程号、响应体。

## P5 敏感度：客户端提速到底有没有用

`bench/academic_latency_sim.py` 加了服务端名额分配规则
（`--allocation fcfs|random|priority`）、事务并发控制（`--lock pessimistic|optimistic`）、
背景竞争者（`--background N`）和蒙特卡洛矩阵（`--p5-matrix --repeats 30`）。
背景竞争者共享同一个 `SeatLedger`，让「相对位次」变成可测量量；不做这一步，
单客户端下 FCFS 恒成功，测不出任何差异。

实测（`seats=3`、`background=12`、每格 30 次独立 seed 取均值）：

| 分配规则 | 锁 | baseline | 高频轮询 | 直连 API | 预构造 | 并发 | speed_gain |
|---|---|---|---|---|---|---|---|
| fcfs | pessimistic | 0.00 | 1.00 | 1.00 | 1.00 | 0.12 | **+1.00** |
| fcfs | optimistic | 0.00 | 1.00 | 1.00 | 1.00 | 0.12 | **+1.00** |
| random | pessimistic | 0.23 | 0.30 | 0.30 | 0.30 | 0.10 | +0.07 |
| random | optimistic | 0.27 | 0.30 | 0.30 | 0.30 | 0.09 | +0.03 |
| priority | pessimistic | 1.00 | 1.00 | 1.00 | 1.00 | 0.12 | +0.00 |
| priority | optimistic | 1.00 | 1.00 | 1.00 | 1.00 | 0.12 | +0.00 |

三条结论：

1. **只有 FCFS 下提速改变相对位次**（+1.00）；random / priority 下提速收益是噪声级或
   严格为 0。P5 是服务端属性，客户端无从优化，只能先判断它属于哪一类。
2. **并发在所有规则下都是最差列**（0.09–0.12），与分配规则无关。
3. **乐观锁降低事务延迟但不提高成功率**：`pulse_burst` 场景切到 optimistic 后
   `confirm_p50` 74.8 → 55.4 ms、503 数 64 → 56，成功率仍 0.01，且产生版本冲突
   （`concurrent` 场景 30 次重复累计 63 次）。瓶颈在排队与限流，不在事务。

矩阵每个单元输出 `min`/`max` 与冲突计数，单次跑是伯努利试验，只有取均值才可解释。

## 与 kingo-assistant 的对应

| 本工程 | 仓库里的位置 |
|---|---|
| `RequestBudget` / `Lease` | `core/request_budget.py`、`docs/contract-request-budget.md` |
| `Transport` 的每次物理请求 | `core/client.py` `KingoClient.read()` |
| `parse_catalog` 的 schema 严格性 | `core/ncwu_timetable_schema.py`、`docs/acceptance/ncwu-xiqueer-har-2026-11-11.md` |
| `Journal` + 名单回读 + `--reconcile` | `core/enrollment.py`、`docs/architecture/automatic-enrollment.md` |
| `ClockSync` / `Prewarmer` | `core/session_probe.py`、`core/preflight.py` 的现有能力延展 |
| `FailoverPlanner` / `FailureClassifier` | `core/enrollment_flow.py` 状态机里「未知结果停住不重试」不变量的落地 |
| `--probe` 输出 | 用来填 `config.example.yaml` 里 `protocol.*` 那些空字段 |

`bench/academic_latency_sim.py` 是离线仿真，用于推导；本工程用于实测。两者共用同一套
四段延迟 / 三种速度定义，可直接把仿真结论拿真实记录去证伪。

## 验证记录

| 命令 | 结果 |
|---|---|
| `kingo-live/selftest.py -v` | 8/8 OK（真实 HTTP、Server-Timing、名单回读） |
| `kingo-live/test_optimizations.py -v` | 13/13 OK（含 HTTP/1.1 持久连接复用） |
| `kingo-live/cli_smoke.py` | 10/10 PASS（含 `--auto --confirm` 全链路、`--probe-save` 落盘） |
| `kingo-live/har_to_schema.py --self-test` | 16/16 checks passed（三接口识别 / 脱敏 / 课表排除 / `\uXXXX` 解码 / `Server-Timing` 不当时钟头 / merge 保留 targets） |
| `bench/academic_latency_sim.py --self-test` | 8/8 checks passed |
| `bench/academic_latency_sim.py --all` | 15 场景，回归数字与上一轮完全一致 |
