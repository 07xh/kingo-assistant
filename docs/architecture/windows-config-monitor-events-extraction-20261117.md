# KingoAssistant 第五批：配置、监测执行器与事件展示分层

日期：2026-11-17

## 依赖审计与拆分原则

在 PR #57 后，`clients/windows/source/windows_gui.py` 约 2504 行。它仍包含
首次运行的配置文件复制、只读监测的异步执行循环、Tk 界面与事件消费。
配置文件的保存、字段校验与原子写入本来已由 `core/desktop_settings.py`
和桌面编辑器管理，没有必要再造一套配置格式。此次分离的是**文件位置、
首次模板初始化、读取顺序与监测运行时**，而不是重写 YAML 合同。

`Application.poll` 同时处理托盘退出、实时监测、官方入口探测、
登录状态、课表和 S202 查询、定时任务完成及窗口退出。如果整体迁移，
很容易改变 `_closing`、`scheduled_worker`、`worker` 的事件顺序。
本次只提取可独立测试的**监测状态与展示格式化**，其他事件生命周期不动。

## 新增模块

1. `windows_config_runtime.py`
   - AppData/打包资源目录的 `app_dir`、`bundled_config`、
     `bundled_demo`；
   - `ensure_user_config`：**仅在不存在时复制模板**，
     不覆盖已有 `config.yaml`，缺少模板时直接报错；
   - `load_validated_config`：保持先 `load_config`、
     正式运行时 `resolve_state_file`、然后 `validate` 的顺序；
     offline 只校验配置，不解析运行状态。
   - `windows_gui.ensure_config`、`read_config` 保留原签名，
     使用极薄转发，仍能单独覆写 GUI 的 `app_dir` 完成离线测试。

2. `windows_monitor_runtime.py`
   - 原有 `MonitorEvent` 数据类和 `MonitorWorker`。
   - 后台线程的停止标志、`asyncio` 事件循环与客户端取消、
     `KingoClient.identity`、校时、`Scheduler.run`、
     状态去重、资源关闭、完成事件及脱敏异常处理**逐字保留**。
   - 保持 `windows_gui.MonitorEvent` 和 `MonitorWorker`
     的公开兼容引用。监测线程仍不访问 Tk 控件。

3. `windows_monitor_events.py`
   - `apply_monitor_status`：对监测状态、变化告警、内存快照和
     表格行执行与原先相同的 UI 线程更新；
   - `format_official_probe_results`：官方登录页探测文本；
   - `scheduled_reason_text`：各类定时任务终止原因的安全文案。
   - 保留 `poll` 的事件消费顺序、轮询间隔、网页登录/会话失效、
     自动任务清理、关闭窗口及按钮恢复逻辑，不进行大规模调度重写。

## 风险控制

- 不改变 `core/desktop_settings.py` 已有的 YAML 保存规范、课程列表、
  用户方案或软件初次启动目录；
- 不改变监测网络请求数、调度器、访问预算、重试策略；
- 不修改任何学校接口参数、登录 Cookie、CAPTCHA 或选课提交；
- 不移动 `native_login`、`ncwu_signed_login` 等认证适配器，
  也不混用根路径 `/` 和 `/hsjw` 的会话；
- `error` 事件只包含异常类名，不能包括 Cookie、响应正文、账号标识；
- `engine_completed` 只是原来离线协议的展示措辞，
  不自动声称学校已选课成功。

## 验证

新增纯离线测试检查首次配置写入不会覆盖已有文件、离线/运行配置的
解析和校验顺序、`MonitorWorker` 的旧引用与异常清理、
`poll` 状态变更及提示文本、官方探测内容、定时任务标签。
既有测试仍应覆盖监测停止、用户权限、登录会话、
S202 实时查询与 60 秒轮询、计划读取和 Tk GUI。

合并条件：分支 Windows Actions 完整通过 unit+selftest+
PyInstaller+packaged EXE selftest+GUI smoke+artifact；然后 squash
合并 `main` 再完成一次相同版本的 CI，才能交付便携版。
