# 仓库清理记录 2026-11-20（自动化审计 + 文档过期修正）

范围：`07xh/kingo-assistant` 全仓库 213 个 Python 文件 + 全部 `.md` / `.txt` 引用。
结论先行：**没有可删的 Python 死代码**；真正过期的是 `QUICKSTART.txt` 里的两处文件引用
与「程序不会自动提交选课」这句已不成立的表述。本轮把清理判据自动化，并修掉过期文档。

## 一、审计方法

`tools/dead_code_audit.py`（本轮新增，纯标准库）：

| 判据 | 含义 |
|---|---|
| ORPHAN | 按 `.py` 入向引用计算，零引用且不是入口/测试/脚本 |
| EMPTY | 0 字节文件 |
| SHIM | 正文只做 `import` / `__all__` 的薄壳 |
| DUPLICATE | 不同路径下同名文件，附体积比与 sha 是否相同 |

复现：

```bash
python tools/dead_code_audit.py --repo . --out dead-code-audit.json
```

## 二、审计结果

| 项 | 数量 | 复核结论 |
|---|---|---|
| Python 文件 | 213 | — |
| ORPHAN 零引用 | 1 | 只有 `tools/dead_code_audit.py` 自身（CLI，仅被 CI 调用），不算死码 |
| 空文件 | 1 | `clients/windows/source/tests/__init__.py`，包标记，必须保留 |
| SHIM 薄壳 | 0（启发式未命中） | 人工复核 `tools/kingo-live/optimizations.py` 为**刻意的兼容薄壳**，把 `ClockSync`/`Prewarmer`/`ReleaseMonitor` 等重导出到 `clients/windows/source/core/selection_optim.py`，删掉会破坏 `tools/kingo-live/*` 与 `test_optimizations.py` |
| DUPLICATE 同名 | 1 对 | `clients/windows/source/core/har_to_schema.py` 与 `tools/kingo-live/har_to_schema.py`，**目标不同**：前者生成内核 `protocol` 配置，后者生成 kingo-live JSON schema，两侧 docstring 已互相注明，不合并 |

附带的入向引用复核：8 个「只被测试引用」的生产模块逐个确认都是入口或惰性导入
（`windows_gui.py` 是应用入口、`date_flip_sync.py`/`window_probe.py` 由监测链路动态调用、
`ncwu_submission_gateway.py`/`ncwu_verified_form_provider.py` 由写入链路按门禁调用），全部保留。

字节相同的文件只有 3 组，且都是 `demo/` 与 `tests/fixtures/` 的同名快照：
`tests/test_repository_layout_cleanup.py` 明确要求 `demo/` 侧必须存在，
`tests/` 侧被离线回放引用，**两侧都不能删**。

## 三、文档过期修正（本轮实际改动）

`clients/windows/source/QUICKSTART.txt`：

| 原文 | 问题 | 改为 |
|---|---|---|
| `详情见 README-Windows.md` | 该文件不存在（实际是 `README.md`） | `README.md` |
| `CHANGELOG-Windows-v4.5.md` | 落后 6 个版本（最新 v5.1） | `CHANGELOG-Windows-v5.1.md` |
| `程序不会自动提交选课` | 与 `core/ncwu_submission_transport.py` 现状矛盾 | 说明写入需四门禁 + 用户授权 + 学校许可同时成立，且只以官方 course+class 回读判定 |
| 标题 `V4.6` | 落后于 v5.1 | `V5.1` |

全仓库文档路径引用共 21 处，其余 19 处是运行时生成物（`config.yaml`、`attestations.json`、
`dist/SHA256.txt`、`capture.har` 等）或外部仓库文件，不属于断裂引用。

## 四、刻意不删的东西

`clients/windows/source/README.md` 已写明的保留策略本轮继续执行：

- `demo/` 三个虚构快照 + `core/demo.py` + `core/offline_selftest.py`：打包后自检的必要资源；
- `tests/`、`tools/verify_windows_build.py`：构建门禁；
- 历史 CHANGELOG（v4.4 / v4.5 / v4.6 / v5.0 / v5.1 / CHANGELOG-Windows.md / CHANGELOG.md）：回退取证；
- 兼容性模块（如 `tools/kingo-live/optimizations.py`）：防止研究 CLI 与客户端行为漂移。

删这些换不来性能，只会让回退少一条证据。

## 五、本轮新增的防线

- CI（`.github/workflows/kingo-live-research.yml`）新增 `Repository dead-code audit` 步骤：
  每次 `tools/**` 变更都跑一次审计并打印 ORPHAN 清单，新增模块忘了接线会立刻现形。
- `tools/patch/` 四个补丁工具接入同一 workflow，四份离线自检成为回归门禁。

## 六、验证状态

- 已执行：`tools/dead_code_audit.py --repo .`（213 文件）、四份补丁自检、`py_compile` 全通过。
- 未执行：`windows-build.yml` 的 PyInstaller 打包（需要 Windows 依赖安装，本轮未触发）。
