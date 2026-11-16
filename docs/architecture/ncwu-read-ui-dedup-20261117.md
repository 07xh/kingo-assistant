# 第二批重构：NCWU 只读会话和 Windows UI 工作线程（2026-11-17）

## 真实审计结论

原始 main 上，`ncwu_s202_round.py`、`ncwu_s202_score.py`、
`ncwu_s202_readback.py` 与 `ncwu_course_plan.py` 分别重复实现：

- 相同的 `httpx.AsyncClient` 超时、关闭跨源重定向、禁用系统代理、
  单连接和 Keep-Alive 设置；
- 将已验证的 EXE 内存 Cookie 按相同域名和 `/hsjw` 路径加入客户端；
- `server_policy.verified` 才允许建立共享服务器请求预算；
- 401/302/303/307/308 映射登录失效，403/429 映射受限，
  且收到 429 后只对已有预算施加 Retry-After，不自动重试；
- 每个 GUI 按钮单独复制了一份忙碌状态判断、禁用/恢复按钮、
  工作线程、异常回退、事件投递以及过期会话清理代码。

这些是可以安全收敛的实现重复，不是独立业务协议。

## 已替换的旧实现

- 新增 `core/ncwu_read_support.py`，只承载四类共享 transport
  基础：`default_read_client`、`set_scoped_cookie`、
  `get_optional_verified_budget`、`classify_read_response`。
  它**不知道任何选课提交、退选或资格预检接口**。
- 四个旧模块删除对应重复 HTTP 初始化、Cookie 与状态分支，
  保留各自的具体 GET/POST、URL、Referer、Accept、GBK 负载、
  `http_factory`/`budget_factory` 可注入参数、模块级
  60 秒手动冷却与 JSON/HTML 业务解析。
- `windows_gui.py` 新增模块内私有函数
  `_ncwu_read_busy`、`_start_ncwu_read_worker`、
  `_finish_ncwu_read_event`。这四个查询入口只传递已有的
  读取函数、原始线程名、事件名、按钮/状态和安全回退类型；
  成功结果仍交由 GUI 线程的事件循环更新。
- 原来的特殊显示行为不变：开课计划读到数据后弹出课程表，
  已选结果页读到候选后显示候选列表；轮次与门数只显示
  对应状态。过期会话仍清空进程内 Cookie 并停止现有轮询。

## 回归门禁

- 新的 transport 测试锁定 Cookie 域和路径、客户端配置、
  仅显式验证预算才调用 factory、HTTP 重定向与 403/429
  的停止行为、Retry-After、响应大小限制。
- 新的 GUI 测试覆盖忙碌状态差异、线程启动失败回滚、
  异步调用异常脱敏、成功后按钮恢复、会话失效及停止轮询。
- 既有 S202 轮次、汇总、开课计划、结果页和 headless Tk
  测试不删，仍从原业务入口模拟学校请求。
- 必须在确切分支上完成 Windows 全量测试、源码自检、
  PyInstaller 打包、独立 EXE 自检及 GUI 冒烟，随后合并，
  并对合并后的 main 再次确认 Windows Artifact。

## 本次不调整

不会重写 `ncwu_live_elective.py` 中特殊 S202 表单初始化逻辑；
不会合并 `ncwu_term_probe.py` 和 `native_timetable.py` 的不同
登录协议/ Cookie Path（其中有些模块使用 `/` 而非 `/hsjw`），
因为简单套用共享实现可能改变实际会话作用域。

也不会删除离线演示、兼容配置、独立 HAR 研究解析器、调度器、
自动选课 FSM 的离线验证和构建打包依赖。完整的自动提交和
`STU_DynamicInitDataAction.do` / `isSelectableSkbjdm.action`
尚未完成真实请求合同验证，本轮不会绕过这些门禁。
