# KingoAssistant Windows｜当前源码与构建指南

> **唯一目标执行架构：EXE 内 HTTP 登录 + 正式 HTTP 提交 + 官方回读。** 浏览器自动点击、浏览器登录混合模式、浏览器预约与 CDP/Playwright 兜底均已退出正式方案。[查看决策](../../../docs/architecture/pure-api-execution.md)。当前正式写接口尚未完成验收，代码中遗留旧 UI 仍需清理。

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

## 配置、隐私与频率

- 首次运行从 `config.example.yaml` 初始化用户设置，已有设置不会被覆盖；配置中的模板课程代码不是实际课程。
- 配置默认保持 `protocol.verified: false` 与 `strategy.dry_run: true`。不能因为“本地模拟通过”就把学校协议标为真实已验收。
- 学校会话信息和敏感 HAR 不应写入 Git、同步盘或公开诊断报告。分享截图或报告前核对学生身份信息已遮挡。
- 请求间隔默认最短 60 秒是**程序的本地限制**，不表示学校批准该频率；如校方规定更长间隔，应遵守校方规则。遇 403/429、登录失效、当前轮次不明或字段冲突就停止，不通过重试或换身份规避限制。
- 旧版“预约打开官网”只属于待删除的历史 UI，不再用作登录、提交或异常兜底；只读监测和离线模拟也不具备正式提交能力。

## 保留的离线测试与历史资料

`demo/` 文件夹中的三个虚构 HTML 快照，以及 `core/demo.py`、`core/offline_selftest.py` **仍是打包后自检的必要资源**，因此不作为“旧无用代码”删除。它们不与真实课程列表混合。既有 `tests/`、`tools/verify_windows_build.py`、历史 CHANGELOG 和兼容性模块也保留，以防新版本回退。

当前仓库构建流程见 [Windows 客户端入口](../README.md)，完整设计及实时接口验收记录见仓库根目录的 `docs/acceptance/`。
