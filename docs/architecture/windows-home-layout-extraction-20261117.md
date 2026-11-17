# Windows 第七批：完整首页视图提取，状态仍归 Application

日期：2026-11-17

## 调用关系与拆分边界

第六批 PR #59 已把「手动官网备用」和「工具与日志」搬入
`windows_page_sections.py`。此后 `windows_gui.py` 中
`Application._build` 仍包含完整首页：本地课程志愿、EXE 登录、
S202 只读课程、轮询监测、预约提交禁用状态以及目标课程列表。

首页的控件密切依赖主控制器中的 `tk.StringVar`、业务方法、
当前会话以及监测/定时任务的禁用状态。把它拆成六个控制器会产生新的
跨模块状态耦合，且无法改善功能验收可靠性。因此本轮只新增一个
`build_home_page(app)`，仍放在已有的单个
`windows_page_sections.py` 中，与另两页的布局函数并列。

`Application._build` 保留顶栏、`ttk.Notebook` 三页顺序、
固定状态页脚和样式定义。完成三页布局时仍使用**同一个**
`Application` 实例；控件保存在 `app.*` 属性上，
不产生新的缓存、状态管理器、事件队列或业务对象。

## 保持不变

- 首页三个实际用户步骤、标题/样式/间距、课程表的五个表头及宽度；
- `edit_button`、`plans_button`、`native_login_button`、
  `native_prepare_button`、`native_clear_button`、
  `timetable_read_button`、`live_catalog_button`、
  `live_poll_button`、`start_button`、`stop_button`、
  `scheduled_start_button`、`scheduled_stop_button`、
  `table` 和 `course_frame` 的公开引用与回调；
- 用户交互仍在主 Tk 线程；`Application.poll` 事件顺序、
  登录态生命周期、监测工作线程、课程配置读写均未改动；
- 自动提交按钮、取消自动计划和初始停止监测按钮保持禁用；
- 表格双击继续调用原 `copy_selected_field('course_id')`；
- 查询频率、学校请求 URL/Cookie、GBK 正选表单、可选预算和写接口
  **均不修改**；未核验的正式选课提交仍然不可用。

## 状态文案修复

顶栏及首页提示过去一直写着「课程目录尚未接通」，
但源代码已经接入 S202 `tableId=2568` **只读**目录解析。
新文案改为「S202 只读读取已实现、待本人账号验收」，
同时保留「自动提交尚未接通」。
这不是宣告学校账号的实时成功，亦不把课程候选和正式教学班确认混淆。

## 验证门禁

- 新增 `tests/test_windows_home_page.py`，无实际 Tk/联网即可检查
  布局区域、控件回调、统一 `StringVar`、原有监测/查询按钮、
  列宽/表头和禁用的未核验提交入口。构建页面不得发起任何查询。
- 加强 `tests/smoke_gui.py` 的实际 Windows Tk 测试，确保首页核心
  控件存在，状态变量仍可用，课程表、禁用态与最低 60 秒刷新提示不变。
- 仍需 Windows CI 的完整离线单元测试、源码自检、PyInstaller、
  打包后 EXE 自检、Tk GUI 冒烟与 Artifact 上传；合并后对
  `main` 的最终构建单独核验。

本次没有修改登录/选课业务，测试通过不等于学校当前开放、真实名额准确
或自动提交能力已经验收。
