# tools/ — 选课链路研究工具（独立于主客户端）

这里放的是**技术研究工具**，不是 KingoAssistant 主客户端的一部分。主客户端
（`clients/windows/` 的 Tk GUI + 只读监测）与这里的 CLI 是两套代码、两套验收口径，
因此刻意放在 `tools/` 下，不进 `clients/windows/source/`，也不参与 PyInstaller 打包。

| 目录 | 内容 |
|---|---|
| `tools/kingo-live/` | 真实 HTTP 客户端：请求预算、时钟对齐、会话预热、释放事件监控、精确目标匹配、备选降级链、失败分类状态机、官方名单回读；HAR → config 字段映射 |
| `tools/bench/` | 离线四段延迟仿真：名额分配规则（fcfs / random / priority）、锁策略、P5 敏感度矩阵 |

## 运行

```bash
# 真实回环 HTTP 端到端（无需任何外部依赖）
cd tools/kingo-live
python selftest.py
python test_optimizations.py
python cli_smoke.py
python har_to_schema.py --self-test

# 离线仿真
cd ../bench
python academic_latency_sim.py --self-test
python academic_latency_sim.py --all
python academic_latency_sim.py --p5-matrix --repeats 5
```

`tools/kingo-live/README.md` 里出现的 `kingo-live/xxx` 一律指 `tools/kingo-live/xxx`，
`bench/xxx` 一律指 `tools/bench/xxx`；`../bench` 在本目录下正好指向 `tools/bench/`。

## CI 分工（避免互撞）

| workflow | 触发范围 | 产出 |
|---|---|---|
| `.github/workflows/windows-build.yml` | `clients/windows/**`、`clients/android/**`、`docs/**`、`README.md`、`SECURITY.md` 的 PR；以及既有的 push 白名单 | 30 分钟 PyInstaller EXE + 原生 GUI 冒烟 |
| `.github/workflows/kingo-live-research.yml` | 仅 `tools/**` 与自身变更 | ubuntu + windows × Python 3.12，纯标准库测试 |

两个 workflow 的 `concurrency` 组互相独立。只改 `tools/**` 的 PR **不会**触发
Windows 打包；只改 GUI 主线的 PR **不会**触发研究工具测试。

## 边界（重要）

- 本目录**不含**任何真实站点地址、真实接口 schema、Cookie 或 token。Cookie 走
  `KINGO_COOKIE` 环境变量注入，提交必须显式带 `--confirm`。
- 全部 55 项断言都是**本机回环 HTTP 靶标 + 离线仿真**，没有对任何真实教务系统
  发过请求。真实 schema 仍需通过 `--probe` 或 HAR 自行对齐。
- 本仓库是**公开仓库**：`tools/.gitignore` 排除了 `config.json` / `capture.har` /
  `*.sqlite` / `records.jsonl` 等本地产物，不要把登录态会话或抓包文件提交进来。
- 这里的代码是延迟分层与成功率归因的研究实现，不是可直接对生产系统使用的
  成品；README 中的策略开关用于对照实验，默认配置为只读 / 单次 / 串行。
