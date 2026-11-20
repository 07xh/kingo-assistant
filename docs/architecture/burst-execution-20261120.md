# 写阶段突发执行（抢课）：为什么默认值不是 1 并发 / 30 s

日期：2026-11-20

## 一、问题

`monitor_policy.ABSOLUTE_MIN_INTERVAL = 30.0` 与
`RequestPolicy.max_concurrency = 1` 原本只有一个使用者：读取监测。
监测跑几小时，每次多读都是学校的查询成本，所以 30 s / 1 并发是对的。

抢课不是同一个问题。窗口只有几十秒，一次尝试是一个很小的 POST，
真正决定成败的是「座位出现的那一秒里，有没有一次请求已经在路上」。
把这两个节奏共用一个旋钮，只有两种结果：

- 按监测的保守值走 → 30 s 一次，等于每个窗口最多两次机会；
- 把监测旋钮拧快 → 监测路径被一起拖进来，且 `validate_limits` 会拒绝。

所以拆开。写阶段有自己的档位、自己的预算、自己的执行器，
监测路径的默认值一个字节都没动。

## 二、新增模块

| 模块 | 职责 |
|---|---|
| `core/rush_profile.py` | 突发档位：并发、间隔、滚动窗口、错峰、退避、熔断、截止 |
| `core/rush_budget.py` | 进程内在途上限 + 每账号 pacer（间隔 + 滚动窗口）+ Retry-After 结转 |
| `core/multi_account_run.py` | 真执行器：每账号一个任务，失败分类，熔断，截止，会话必关 |
| `core/multi_account_cli.py` | `--multi-run` 入口：`--dry-run` / `--live` / `--bench` |
| `core/rush_read.py` | `drive_read_cadence: true` 时把读取轮询切到突发间隔 |

`GlobalRequestBudget` 没有改：每次发送仍然在传输层经过它，
服务端窗口取两者中更严的那个。突发只是额外加了一层「别让本机排队」。

## 三、三档预设（实测，不是估算）

`--bench` 用合成会话跑完整调度链路（错峰 → pacer → 在途上限 → 退避），
单次 POST 模拟 0.35 s，3 个账号、每账号 12 次：

| 档位 | 并发 | 生效间隔 | 发送 | 耗时 | 次/秒 | 次/分 | 相对串行 |
|---|---|---|---|---|---|---|---|
| safe | 2 | 1.00 s | 36 | 12.27 s | 2.93 | 176 | 90× |
| standard | 8 | 0.40 s | 36 | 5.10 s | 7.06 | 424 | 225× |
| extreme | 16 | 0.10 s | 36 | 4.43 s | 8.13 | 488 | 900× |

串行基线（1 并发 / 30 s）是 2 次/分。standard 单账号计划速率 150 次/分，
三账号合计 424 次/分（受 0.35 s 模拟时延与 36 次总量限制）。

生效间隔 = `max(min_interval, window_seconds / window_requests)`，
所以「声明一个自己会立刻超出的窗口」在构造阶段就会被拒。

## 四、多账号：从计划到执行

原先 `AccountPlan` 只接受 `max_concurrency=1`，`multi_account_plan` 也只做校验。
现在：

- `AccountPlan.max_concurrency` 允许 1–16（每个账号独立数据库，不存在共享租约）；
- `MultiAccountRunner` 真的并发跑：每账号一个 asyncio 任务，
  错峰 `index * stagger_ms` 启动，共享进程内在途上限，
  每账号公平份额 `concurrency // 账号数`（至少 1）；
- 账号级隔离：一个账号触发人机校验只熔断它自己，其他账号继续；
- 会话必关：正常结束、异常、外层取消三条路径都走 `finally`。

失败分类（`classify_failure`）：

| 分类 | 判据 | 动作 |
|---|---|---|
| `duplicate` | 已选/重复/已存在 | 停止重试，记为已持有 |
| `challenge` | 验证码/滑块/captcha | 立即熔断该账号（重试只会消耗锁号计数） |
| `throttled` | 429/限流/Retry-After | 兑现 Retry-After（受 `retry_after_cap` 限制）后重试 |
| `transient` | 5xx/超时/连接重置 | 指数退避 + 抖动 |
| `fatal` | 其他 | 停该目标，账号继续 |

## 五、用法

```powershell
# 实测吞吐（不联网）：先知道自己机器上到底能打多快
python main.py -c config.yaml --multi-run --bench
python main.py -c config.yaml --multi-run --dry-run --preset extreme

# 真实提交：每个账号必须声明自己的会话工厂
# accounts[].session_factory: "my_adapter:make_session"
python main.py -c config.yaml --multi-run --live
```

`--live` 下没有声明工厂的账号会报 `no_session` 并**不被联系**，
不会把「没有会话」伪装成「没抢到」。

## 六、读取节奏

`rush.drive_read_cadence: true` 时，`Scheduler._next_interval` 直接返回突发间隔
（带抖动，下限 0.05 s，上限 60 s），不再用 `strategy.interval_range` 的 30 s 下限。
默认 `false`：监测跑几小时的场景仍然按声明范围走。

## 七、测试

新增 4 个测试文件，74 条：

- `tests/test_rush_profile.py`（23）：档位、上限、未知键、窗口简写、倍数换算
- `tests/test_rush_budget.py`（13）：间隔、窗口封顶、Retry-After、在途上限、公平份额
- `tests/test_multi_account_run.py`（21）：提交、重复、验证码熔断、429、熔断器、截止、取消、会话关闭、分类器
- `tests/test_rush_read.py`（17）：默认不接管、显式接管、抖动带、调度器接线

受影响的既有测试（`test_multi_account_plan`、`test_policy_limits_override`、
`test_contract_http`、`test_har_form_*`）132 条全过。

## 八、仍未闭环

真实提交需要每个账号自己的会话材料（Cookie、journal、form provider）。
执行器接受任意实现了 `async submit(target)` / `async aclose()` 的对象，
`--live` 通过 `session_factory` 注入；缺这段适配器时，跑不了真实 POST，
这一条不是代码问题，是缺一次成功写操作的会话证据。
