# Windows 页面布局第六批重构（2026-11-17）

> **历史 UI 记录，不再作为当前实施方案。** 2026-11-20 纯接口架构已取消“手动官网备用”、浏览器预约及浏览器登录混合路线；这些旧页面仅在旧版本源码/截图中可见，必须逐项清理。当前唯一规范：[纯接口执行架构](pure-api-execution.md)。

## 审计结论

在第五批 PR #58 合并的主分支，`windows_gui.py` 约 2329 行；
`Application._build()` 约 366 行。这个函数混合了三个用户路径：

- **首页**：目标课程设置、原生登录、S202 只读查询、余量监测与课程列表，密集依赖主应用状态、按钮生命周期和课程表 `Treeview`；
- **手动官网备用**：官网打开/复制课程、预约到点提醒和登录状态检测，只有布局及对主应用原有回调的引用；
- **工具与日志**：离线 HAR、学期/选课表单核验、只读结果、调试配置和本地日志，属于独立导航页，业务逻辑仍由主应用提供。

为了避免过度拆分，只抽出后两页为 **一个模块**：
`clients/windows/source/windows_page_sections.py`。
首页及三个 tab 的创建顺序、顶栏与页脚、按钮禁用与状态变量仍由
`Application` 初始化，不增设第二个状态存储。

## 具体修改

1. `build_manual_page(app)` 原样保留手动官网页的控件布局、按钮标签、
   `portal_panel`、`portal_appointment_button`、
   `session_check_button` 等公开控件引用；
2. `build_tools_page(app)` 保留原有三行诊断工具按钮、只读 S202/课程计划
   结果按钮、学期探测、HAR 协议报告、配置文件栏和日志；
   `advanced_panel` **默认不显示**，仍由
   `Application.toggle_advanced_panel` 控制；
3. 原方法 `Application._build` 在首页结束后只调用这两个页面构建函数；
   不再复制布局代码。原本 `app.status`、登录状态、配置路径、
   事件队列、调度器和所有按钮回调仍归 `Application` 所有。
4. 没有重新创建新的父窗口、后台线程、网络适配器或单独的数据状态；
   页面导航仍由已有 `ttk.Notebook` 进行。

## 回归测试

- `test_windows_page_sections.py`：用纯 Mock 的 Tk 创建器核对
  “手动官网备用”四个回调与全部调试入口字段；布局时不得发起网络、
  不触发写接口，`scheduled_preflight_button` 和状态绑定仍存在；
  高级配置默认不调用 `pack`。
- `tests/smoke_gui.py`：在真实 Windows Tk 环境中验证三个 tab、
  必需按钮和配置/日志组件仍存在、日志只读、状态字段正确；
  高级配置可展开和收起，手动页导航正确，自动提交按钮保持禁用。
- 保留所有原有 `unittest`、源码自检、完整 PyInstaller、
  打包后 EXE 与 Tk GUI 冒烟测试。

## 不在本轮处理

不拆分首页的登录/监测业务到多个 UI 类；
不更改 `Application.poll` 事件顺序、登录状态及后台工作线程；
不更改学校 S202 或其他协议路径、Cookie、请求间隔；
不启用尚缺真实接口协议和本人账号验收的选课写入。
学校接口“仅观察到字段名”仍不能视为可用。

仅当分支的全部 Windows CI 测试/EXE 构建成功后合并；
主分支再重新验证同一提交的可下载 Artifact。
