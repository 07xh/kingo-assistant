# tools/patch/ — 写协议取证与限速策略补丁

这里是**针对主客户端的外挂式补丁层**：不改 `clients/windows/source/core/` 的默认行为，
只在需要时把「写入门禁」「轮询节奏」「并发/频率」「多账号」四个开关拨到目标档位。
与 `tools/kingo-live/` 一样，不参与 PyInstaller 打包，纯标准库。

| 文件 | 作用 | 自检 |
|---|---|---|
| `p0_protocol_recon.py` | 从真实 HAR 还原 S202 七字段写入合同，产出 `items_binding.json`（字节即哈希）+ 合同文档 + attestation 登记脚本 | `python p0_protocol_recon.py selftest` |
| `p1_polling_plan.py` | 按轮次时间生成 `strategy.pickup.peak_windows`；同课程备选教学班扩散；全量目录 diff | `python p1_polling_plan.py selftest` |
| `v1v2_limit_lift.py` | 对本地 clone 打 5 处补丁：并发上限 1–16、多并发跳过跨进程租约、`ABSOLUTE_MIN_INTERVAL` 读环境变量；带备份/还原 | `python v1v2_limit_lift.py selftest` |
| `v4_multiaccount.py` | 账号级编排：独立 SQLite 预算、出口降权、鉴权熔断、抢到即停 | `python v4_multiaccount.py selftest` |

## 为什么按这个顺序

仓库自带 bench 的实测结论决定了优先级：

- 发现延迟主导：baseline 轮询 ~28 s，正确轮询 ~281 ms，submit ~20 ms → **先优化发现**。
- 并发是负收益：8 并发让成功率 1.00 → 0.125，p95 70 ms → 1178 ms → **并发只对多账号有意义**。

所以顺序是：能不能提交（P0）→ 能不能发现（P1）→ 能不能更快更猛（V1/V2/V4）。
反过来做，后面全是空转。

## P0 的真实用法

```powershell
# 需要一次真实成功正选的 HAR（浏览器 DevTools → 全部保存为 HAR），
# 以及选课目录导出（含 skbjdm / class_id），否则映射证据不成立、不签发 attestation
python p0_protocol_recon.py scan --har capture.har --catalog catalog.json `
  --repo ..\.. --reviewer <你的标识>

python p0_protocol_recon.py verify --binding evidence\items_binding.json
```

产物：

- `evidence/items_binding.json` —— sha256 就是 `PreparedNCWUSubmission.item_binding_sha256`
- `evidence/official_write_contract.md` —— 注册为 `official_write_verified` 的验收证据
- `evidence/attest_issue_official_write.py` —— 跑一次把该门禁置为 valid

没有 catalog 命中时不生成登记脚本，只落数据。这是刻意的：`items` 与内部教学班 ID 的
映射拿不出证据，就不该让门禁通过。

## P1 的真实用法

```powershell
python p1_polling_plan.py plan --open 2026-11-23T08:00 --deadline 2026-11-27T22:00 `
  --supplemental 2026-11-28T08:00 --supplemental-end 2026-11-28T12:00 --out pickup.yaml

python p1_polling_plan.py widen --catalog catalog.json --course K0012345 --exclude <已锁 class_id>
python p1_polling_plan.py diff  --before snap1.json --after snap2.json
```

`plan` 排四个窗口：开抢首波 30 分钟、开抢后 2–4 小时退选潮、截止前两小时、补选首日。
默认高峰 30 s / 平时 60 s，受 `ABSOLUTE_MIN_INTERVAL` 约束；要更小必须先打 v1v2 补丁。

## V1/V2 与 V4

```powershell
python v1v2_limit_lift.py status --repo D:\code\kingo-assistant
python v1v2_limit_lift.py apply  --repo D:\code\kingo-assistant --concurrency 4 --floor 2
$env:KINGO_MIN_INTERVAL_SECONDS = "2"      # 不设则为 30，行为与官方一致
python v1v2_limit_lift.py revert --repo D:\code\kingo-assistant
```

`v4_multiaccount.py` 是库，不是 CLI：注入你自己的 `async def probe(Attempt) -> ProbeOutcome`
即可，它负责每账号独立预算、出口轮换、熔断、抢到即停。

## 边界

- 四个自检全部**离线**：P0 用内置合成 HAR，P1 用内存目录，V1/V2 在临时目录改假文件，
  V4 用假 probe。不向任何真实教务系统发请求。
- 本目录不含真实站点、Cookie、token、学号。P0 的合成样例域名是 `jwmis.example.edu.cn`。
- `v1v2_limit_lift.py` 只改本地 clone，且要求 5 处目标文本逐字命中；源码版本不匹配时
  整体放弃，不产生半改状态，`revert` 可逐字节还原。
