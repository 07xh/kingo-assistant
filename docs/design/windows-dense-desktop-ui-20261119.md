# Windows 密集工作台

当前 Tk 客户端通过 `windows_gui.py` 的同一个 Application 挂载「选课工作台」和「工具与日志」。上方命令条保留使用说明，底部状态栏显示当前运行状态。

- 「选课工作台」保留本机课程计划、本人账号、课程数据和只读监测。表格与较长提示可独立滚动，定时任务及提交门禁区固定在其下方，缩小窗口也能看到。
- 「工具与日志」保留已核验接口的读取、配置和离线诊断。新版复用现有 Application 回调、状态和会话，挂载 UI 本身不会触发学校请求。
- 保留现行 [纯接口执行架构](../architecture/pure-api-execution.md) 的验收边界。真实提交仍需证据门禁，不由 UI 开启。

默认大小 1180×790，最小 960×640。使用微软雅黑 UI、浅灰分组、白色表格和蓝色主按钮。历史深色侧栏及旧版开发稿可在仓库历史中查看。

从 `clients/windows/source` 运行 `python -m unittest discover -s tests -q`、`python windows_gui.py --self-test` 和 `python tests/smoke_gui.py`。独立 EXE 构建及运行检查见 [Windows 工作流](../../.github/workflows/windows-build.yml)。这些检查不认证线上选课能力。
