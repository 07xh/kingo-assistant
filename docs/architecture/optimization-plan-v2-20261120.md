# 优化计划 v2：按审视报告落地的五项代码改善（2026-11-20）

上游依据：`docs/architecture/optimization-plan-v1.md` 与
`docs/acceptance/` 的接口清单。v1 的结论是「代码不是瓶颈，证据链与写协议才是」。
本轮不重复造已实现的能力，只把 v1 里 **能用代码解决的部分** 全部落进 `core/`，
并且让每个新判定都可离线复现。

一句话：把「外挂式补丁工具」升级为「产品内的可验证路径」，把「不可配置的硬编码」
升级为「有审计痕迹的显式声明」。

---

## 1. P0｜写协议：HAR × 证据 的配对桥

### 问题

`tools/patch/p0_protocol_recon.py` 从 HAR 提取的证据文件是**脱敏的**：只有
`items.value_sha256`、`hid_option_sha256`、`student_id_sha256`，没有明文。
这是对的（证据不该存明文），但它留下一个空档：

> 证据文件证明「抓到过什么」，却无法证明「现在这份 HAR 就是它」。

`VerifiedS202FormProvider` 需要 `CurrentS202FormObservation`，其中
`binding_evidence` 必须是原始字节。此前没有任何代码把这两端接起来。

### 落地方案

新增 `core/ncwu_har_form_evidence.py`：

| 函数 | 作用 |
|---|---|
| `load_items_binding(path)` | 读证据文件，拒绝 `mapping_proven=false` / 弱匹配（`skbh`）/ 多课程 `items` / 非 S202 |
| `extract_submit_form(har)` | 从 HAR 取出唯一的 `STU_ElectCourseAction.do` POST；多个不同样本直接报错 |
| `verify_pairing(form, binding)` | 只做哈希配对：`items` / `hidOption` / `xh` / `xn` / `xq` / `lcid` |
| `cross_check(form, binding, scope, target)` | 配对 + 会话绑定（学号/学年/学期/轮次/课程/教学班） |
| `prepare_from_files(...)` | 一步产出 provider 可接受的 observation |

新增 `core/har_form_cli.py` + `main.py --har-form`，两级校验：

```powershell
# 一级：只验「这份 HAR 和这份证据是不是同一次提交」（不需要登录）
python main.py --har-form --har-file capture.har `
    --har-evidence evidence/items_binding.json --har-json

# 二级：再加会话值，验证 provider 会不会接受（仍然不发请求）
python main.py --har-form --har-file capture.har `
    --har-evidence evidence/items_binding.json `
    --har-course K0012345 --har-class 3141592653589 `
    --har-scope "acct-A" "2026-2027-1" "20260001" "2026" "1" "S202" --har-json
```

退出码：`0` 通过，`2` 参数/文件问题，`3` 哈希或绑定不一致，`4` 会话值不匹配。

### 判据

一级校验抓的是「改了证据」「抓了第二次」；二级校验抓的是「复用旧会话」。
两者都通过后，`PreparedNCWUSubmission` 才有 `item_binding_sha256` 可填。

---

## 2. P1｜席位发现：事件上限 + 同课程扩散 + 当前可抢

### 问题

`ReleaseMonitor.events` 在整场放号里无上限增长。一个跑满 8 小时的 EXE 会把它当
内存泄漏慢慢吃光，而它本来只是给 UI 显示「刚才发生了什么」。

同时 v1 指出「扩大备选」是纯收益：目标班满了，同课程别的班可能有座，但此前只能
重新读目录才知道。

### 落地方案（`core/selection_optim.py`）

| 新增 | 语义 |
|---|---|
| `ReleaseMonitor(max_events=2000)` | 环形保留最近 N 条，`dropped_events` 计数 |
| `available_now(min_remain=1)` | 当前真有座的 offering，按余量降序；`remain=None` 不算有座 |
| `widen_suggestions(course_id, exclude_class_ids=..., limit=5)` | 同课程备选，按余量降序，可排除已持有班级 |
| `PollingSchedule.to_config()` | 把策略写回 `strategy.pickup`，与 `from_config` 成对，避免读写两侧漂移 |
| `PollingSchedule.report()` | 增 `active_window` / `effective_interval_s` |

`widen_suggestions` 只跨教学班，不跨课程——一次提交只能是一个课程的一个内部班级。

---

## 3. P2｜多账号：配置即校验

### 问题

`GlobalRequestBudget` 的「跨进程只允许一个请求」只对**同一数据库路径**生效。
两个账号共用一个 `.sqlite` 会互相串行；同一 `(course, class)` 出现在两个账号会
产生跨账号无法去重的重复提交。此前这些都没有在配置层拦。

### 落地方案

新增 `core/multi_account_plan.py`：`accounts` 段 → 每账号独立 `AccountPlan`。

拒绝清单：

| 情况 | 原因 |
|---|---|
| 两个账号同一 `database`（含大小写差异） | 滑窗会把 B 排在 A 后面 |
| `:memory:` 或相对路径 | 跨进程锁退化为无效 |
| 同一 `(course_id, class_id)` 出现在两个账号 | 重复提交无法跨账号去重 |
| 代理 scheme 不在 `http/https/socks5/socks5h` | 会回落到系统代理，两个账号同出口 |
| 两个账号同一代理 | 同上 |
| `max_concurrency != 1` | 并发是 `RequestPolicy` 的所有权，规划层不越权 |
| 账号 `base_url` 与共享策略不一致 | 同一份预算不可能服务两个 origin |

`summarize(plans)` 输出脱敏摘要，可直接进日志或 UI。

---

## 4. P3｜挑战检测：停机而不是重试

### 问题

`FailureClassifier` 只有 6 类。验证码/滑块/短信/CAS 重认证的响应通常是 `200` +
表单正文，于是会落到 `pending` → `halt`，或者更糟：落到 `backoff` 被当成临时故障
反复重试，把「可解一次」变成「账号锁定」。

### 落地方案（`core/selection_optim.py`）

| 新增 | 语义 |
|---|---|
| `DEFAULT_CHALLENGE_MARKERS` | 中英双语标记：验证码/滑块/短信/动态码/统一身份认证/`captcha`/`sms code`/`session expired` 等 |
| `Decision.kind = "challenge_required"`, `action = "halt"` | 明确停机，交给人工 |
| 检查顺序 | **先挑战、后状态码**：`403 + 验证码` 不能变成 `auth` |
| `business.challenge_markers` | 可覆盖默认集 |
| 不消耗退避阶梯 | 挑战不是重试，`RetryBackoff.attempts` 保持不动 |

这一项不实现任何绕过：识别到挑战就停，把「怎么解」留给操作者。

---

## 5. P4｜两条被下调的阈值：从「改源码」变成「显式声明」

v1 把「拆 30s floor」和「解除 max_concurrency=1」列为违规级。直接改常量意味着
行为变更没有任何痕迹。本轮把它改成**可配置但有审计**：

### 5.1 轮询下限（`core/monitor_policy.py`）

```yaml
strategy:
  interval_range: [10, 60]
  official_frequency_policy:
    manually_confirmed: true
    source: "教务处 2026-11 公告：允许 10 秒间隔"
    min_interval_seconds: 10
    absolute_floor_seconds: 10
    absolute_floor_verified: true
    absolute_floor_source: "同上"
```

- 默认下限仍是 30s；`MIN_OVERRIDABLE_FLOOR = 5.0` 是绝对不许破的底线
- 三个键缺任一即报错（而不是忽略），所以打错字不会静默变「安全」
- 未完成 `manually_confirmed` 时给 `absolute_floor_seconds` 直接拒绝
- `MonitorLimits.floor` / `faster_than_default_floor` 供 UI 显示「当前已低于默认值」

### 5.2 请求并发（`core/request_budget.py`）

```yaml
server_policy:
  max_concurrency: 4
  parallel_lease_attested: true
  parallel_source: "四个账号各自独立预算库"
```

- 1 时行为与之前**逐字节一致**：仍持 OS 独占租约
- 大于 1 必须同时给 `parallel_lease_attested` 与 `parallel_source`，否则 `PolicyError`
- 大于 1 时不取独占租约（否则等于没放开），但滑窗仍按机器总量计数
- `policy` 表的签名包含并发数：旧库切到并发策略会被要求先核对既有配额

---

## 6. 测试

新增 6 个离线测试文件，全部不联网、不依赖 tkinter、不写仓库：

| 文件 | 覆盖 |
|---|---|
| `tests/test_failure_challenge_backoff.py` | 挑战识别、`403+验证码` 优先于 auth、退避阶梯与上限、不消耗阶梯 |
| `tests/test_release_monitor_widen.py` | 事件上限与丢弃计数、`available_now`、`widen_suggestions` 排序/排除/不跨课程 |
| `tests/test_har_form_evidence.py` | 哈希配对、多项拒绝路径、`repr` 不泄露字段值 |
| `tests/test_har_form_cli.py` | 两级校验退出码、JSON 输出、`main.py` 端到端、无 config 也能跑 |
| `tests/test_multi_account_plan.py` | 数据库/目标/代理/来源/窗口冲突、摘要脱敏 |
| `tests/test_policy_limits_override.py` | 下限三键校验、并发声明校验、并发策略跳过租约、签名随并发变化 |

运行：

```powershell
cd clients/windows/source
python -m unittest discover -s tests -q
```

---

## 7. 未做，以及为什么

| 项 | 状态 | 原因 |
|---|---|---|
| 真实 NCWU 提交 | 未执行 | 需要一次真实的成功 HAR；代码路径已就绪，`--har-form` 可先离线自检 |
| `attestation` 实际签发 | 未执行 | 签发是人工复核动作，`attest issue` 需要证据文档与 reviewer |
| 验证码/滑块自动识别 | 未做 | 识别到挑战即停机；自动解会让「可解一次」变成「反复触发」 |
| `machineList` 等未验证端点 | 未接 | 未在本仓库验收清单内，不猜路径 |
| 并发默认值上调 | 未做 | `tools/bench` 已证同一目标并发是负收益；只有多目标多账号才需要，且必须显式声明 |

顺序仍然是 v1 的那句：**先能提交 → 再能发现 → 最后才是更快更猛。**
