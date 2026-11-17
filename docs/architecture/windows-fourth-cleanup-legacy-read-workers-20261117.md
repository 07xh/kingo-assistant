# KingoAssistant 第四批旧代码清理｜只读窗口组件与旧接口工作线程

日期：2026-11-17。

## 本轮真实源码审计

上一批 PR #55–#56 已统一 S202 正选、轮次、门数、已选结果和开课计划的
只读 HTTP 客户端及 Windows 工作线程。最新主分支仍保留两种可安全清理的重复：

- `windows_gui.py` 内的 `_open_readonly_popup`、`_create_readonly_tree`
  属于无网络、无状态的 Tk 窗口组件，与主控制器代码耦合无必要。
- `check_known_timetable`、`check_known_year_term`、
  `check_known_school_results` 分别复制了
  设置忙碌标志、禁用按钮、`asyncio.run`、捕获异常转换为安全结果、
  线程启动失败恢复按钮和向队列提交事件的代码。

**不合并它们的 HTTP 适配器：** `native_timetable.py`、
`ncwu_term_probe.py` 和 `known_school_pages.py` 使用
`/hsjw` 之外的根路径 Cookie 以及各自独立的表单验证/限制策略。
直接套用 S202 `/hsjw` 客户端会改变浏览器 Cookie 的路径匹配、
会话作用域和原有鉴权边界，因此本轮仅统一 UI 工作线程生命周期。

## 实际修改

1. 新建 `clients/windows/source/windows_readonly_widgets.py`，移动两个
   Tk 只读组件函数。原 `windows_gui` 仍导入并保留同名函数引用，
   避免影响已有调用和 GUI 测试。静态弹窗、课程计划/结果列表的布局未改变。
2. 三个历史只读入口现在使用已验证的
   `_start_ncwu_read_worker`，分别保持原线程名、事件名、
   会话获取方式、原有运行状态和启动失败提示。
3. `check_known_year_term` 的离线 `validate_term_contract` 和
   `check_known_school_results` 的 `validate_known_page_contract`
   继续在创建工作线程之前执行，不会误发未核验的请求。
4. 学校已选结果入口仍将固定的 `selected_results` 页面类型
   传给原读取函数，异常时的回退结果也保留相同页面类型。
5. 课表、学期与页面事件处理仍使用原来代码，不自动改变会话失效
   对轮询的处理；正式提交、自动抢课、验证码与登录完全未改。

## 新增回归测试

- 三个入口的原线程名、事件名与失败类型；
- 未核验协议时零请求，已选页面 `selected_results` 参数保持；
- 异常不暴露 Cookie、会话或服务器请求内容；
- 线程启动失败时按原顺序禁用再恢复按钮；
- 窗口组件独立文件确实没有 HTTP/会话读取依赖，旧函数引用仍有效。

## 验收门禁及未解决事项

仅合并 Windows Actions 全量单元测试、源码自检、PyInstaller、
独立 EXE `--self-test`、Tk 界面冒烟测试和工件上传均成功的分支。
合并后需要在 `main` 再执行相同的 Windows 构建。

自动选课关键接口 `STU_DynamicInitDataAction.do`、
`isSelectableSkbjdm.action` 和正式提交的请求/结果契约仍需
本人授权的现场证据。本轮离线 CI 不代表这些学校接口已经可用。
