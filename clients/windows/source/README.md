# KingoAssistant Windows｜当前源码与构建指南

这是独立 Windows 10/11 Python + Tk 桌面客户端的正式源码目录，包含 **EXE 自有登录、真实只读查询、离线志愿准备、错误分类与构建验收**。它不依赖 Android App 或浏览器点击自动化。历史 V4.6 压缩包与嵌套 Actions 配置已停止维护；本目录的源码才是当前唯一构建源。

## 运行与构建

- **使用已有便携版：** 在仓库根目录 [Windows CI](https://github.com/07xh/kingo-assistant/actions/workflows/windows-build.yml) 选择成功的 `main` 构建，下载 `KingoAssistant-Windows-portable`。
- **运行源码：** Windows 10/11、Python 3.10+，双击 `run-windows.bat`。
- **本机构建：** 双击 `build-windows.bat`；它创建虚拟环境、安装依赖、运行完整离线测试和源码自检，再通过 PyInstaller 打包，随后验证打包后的 EXE 和 Tk GUI。
- **检查产物：** `dist/KingoAssistant-Windows.exe`、`dist/SHA256.txt`、`dist/build-verification.json` 必须对应同一次构建。失败会删除可能残留的旧文件。
- **源码诊断：** 启动故障可使用 `debug-windows.bat`；执行 `python -m unittest discover -s tests -q` 可独立运行离线测试。

**正式 Actions 工作流仅在仓库根目录 `.github/workflows/windows-build.yml`。** 不需要解压历史源码包，或把另一套工作流复制进 `source/.github/`。它在 Windows runner 完成单元测试、PyInstaller、打包后 `--self-test` 和 GUI 冒烟测试；通过仅说明离线构建可靠，不代表学校账号/真实选课提交已验收。

## 软件现有能力

**登录和当前会话。** EXE 在本机执行华水已识别的签名登录流程，核对身份后维持进程内会话。学校的其他 CAS 登录场景须与当前入口分开处理。登录失败时不得继续复用过期 Cookie。

**学校只读接口。** 在「工具与日志」及相应页面可以使用：

- S202 轮次、时间窗状态诊断，及 `DataTable.jsp?tableId=2568&fre=1` 正选课程目录；
- `DataTable.jsp?tableId=5327042` 开课计划，展示校方当次返回的课程、教学班与计划人数；
- S202 已选结果候选记录及官方已选门数汇总；
- 开课计划与本地志愿按学期、课程代码和学校教学班标识进行离线核对；
- S202 课程目录与本地志愿关联，标记**课程候选**，但不将显示班号 `skbh` 当成内部 `skbjdm`；
- 本人本地 HAR 的接口字段核验与脱敏报告，及原有只读监测和预约准备功能。

以上功能均需本人账号进行实地返回值验收。**开课计划 `qdrs` 是查询时的快照，绝不是开放时实时剩余名额；课程候选、页面 HTTP 200 和已选门数均不能单独证明某个教学班已经选中。**

**正式提交尚未接通。** `STU_DynamicInitDataAction.do` 和 `isSelectableSkbjdm.action` 尚缺完整实时请求体与业务响应证据；不猜测参数、不启用选课或退选写接口。

## 网络与时间面板（只读测量，工具页）

「工具与日志」页顶部新增一张卡片，把三项**与学校业务无关**的本机测量集中到一处。三者都不在启动时自动执行，都需要点按钮：

| 测量 | 做什么 | 不做什么 |
|---|---|---|
| 节点测速 | 对粘贴的候选主机做 TCP+TLS 握手（3 轮），按中位耗时排序，失败率 >50% 的节点剔除 | 不发业务请求、不查课程、不提交 |
| 时钟校准 | 读响应头 `Date` / `Server-Timing`，多轮取样取中位数，进程内维护 offset | 不改系统时钟（不调 `SetLocalTime`，不需要管理员） |
| 会话守护 | 周期 HEAD 探活，把响应分成 ok / 掉线 / 限流 / 网络异常 | 只有明确「登录失效」才尝试重登；429/5xx 与超时只退避 |

实现位置：`core/netops/`（纯标准库，无第三方依赖）+ `windows_netops_panel.py`（Tk 面板，后台线程只通过队列回主线程）。离线测试见 `tests/test_windows_netops.py`（31 条，含真实 Tk 控件测试）。

两点边界必须先说清：

- **精度**：HTTP `Date` 只有秒级精度，offset 理论误差 ±500ms 加半个 RTT。要进 ±50ms 需把校准地址指向返回毫秒字段的接口；`core/time_sync.py` 的 `TimeSync`（NTP + HTTP 双采样，带 uncertainty）精度更高，但命令行侧使用，GUI 面板当前不共用。
- **重登**：本客户端没有经过验收的登录协议适配器，所以会话守护的 `relogin` 固定返回失败并在状态行写明「未接入落实验收的登录协议」。探活与分类是真实的，重登是诚实的失败。

## 配置、隐私与频率

- 首次运行从 `config.example.yaml` 初始化用户设置，已有设置不会被覆盖；配置中的模板课程代码不是实际课程。
- 配置默认保持 `protocol.verified: false` 与 `strategy.dry_run: true`。不能因为“本地模拟通过”就把学校协议标为真实已验收。
- 学校会话信息和敏感 HAR 不应写入 Git、同步盘或公开诊断报告。分享截图或报告前核对学生身份信息已遮挡。
- 请求间隔默认最短 60 秒是**程序的本地限制**，不表示学校批准该频率；如校方规定更长间隔，应遵守校方规则。遇 403/429、登录失效、当前轮次不明或字段冲突就停止，不通过重试或换身份规避限制。
- 本机后台监测、预约 UI 和研究性模拟演示并不具备真实选课提交能力。

## 保留的离线测试与历史资料

`demo/` 文件夹中的三个虚构 HTML 快照，以及 `core/demo.py`、`core/offline_selftest.py` **仍是打包后自检的必要资源**，因此不作为“旧无用代码”删除。它们不与真实课程列表混合。既有 `tests/`、`tools/verify_windows_build.py`、历史 CHANGELOG 和兼容性模块也保留，以防新版本回退。

当前仓库构建流程见 [Windows 客户端入口](../README.md)，完整设计及实时接口验收记录见仓库根目录的 `docs/acceptance/`。
