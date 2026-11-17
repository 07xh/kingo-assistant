# 第八批质量收尾：Windows 会话状态、监测停止与重启

日期：2026-11-17。针对 `main` 提交 `b4165151` 的实际源码审查。

## 已确认的行为风险

1. `clear_native_session` 原先仅检查部分后台读取标志，缺少
   `_live_catalog_in_progress`、`_s202_result_in_progress`、
   `_s202_round_probe_in_progress`。这使得用户可在 S202 只读请求
   尚未结束时清除进程内 Cookie，造成 UI 认为当前会话无效而已启动
   请求继续使用原有会话的状态混乱。
2. `start_monitor` 先把新 `MonitorWorker` 放入 `self.worker`，
   随后才调用 `worker.start()`。标准线程启动如果抛出
   `RuntimeError`/`OSError`，`self.worker` 一直保留非空引用，
   “重新开始”按钮会被已有 worker 检查挡住。
3. 历史课表、学期与学校 HTML 页面使用自行维护的忙碌状态判断，
   没有检查全部 S202 任务；例如一个历史接口请求可能与 S202
   实时查课并发，随后有一条过期事件清掉另一操作的身份状态。
4. 各页面接到 `expired` 后只清理 Cookie 或更新本页文案，
   不总会同步撤销先前“已验证课程”的 UI 提示和停止手动启用的
   定时刷新，容易造成“看起来仍已登录”的假象。
5. 部分旧模块重构后仍有未使用的导入，增加维护干扰。

## 修复

- 将 5 个 S202 及 3 个旧只读入口的运行标志统一列入
  `_NCWU_READ_FLAGS`，用 `_native_read_in_progress` 与
  `_ncwu_read_busy` 检查新登录、会话核验、历史读取、S202 读取、
  清除登录、监测启动。**不改变各接口 Cookie 域、路径、请求内容或
  单独的 HTTP 预算。**
- “清除登录”会先停止之后的自动刷新；如果仍有已发送请求在执行，
  会明确提示“结束后再次点击”，不谎称已清理。下次显式点击在
  已无任何后台任务时完成仅**内存**中的会话清除（不是校方注销）。
- `_invalidate_native_session` 是唯一的服务端过期处理路径：
  清掉 Cookie 与来源，更新登录文案，撤销以前的课程核验提示，
  停止已授权的 S202 自动刷新。保留每个接口原来的具体错误文案。
- 监测启动先局部构建并 `worker.start()`，成功后才赋值
  `self.worker` 和禁用 UI；线程创建失败则留在可重试状态，
  既不存储异常细节也不保留虚假的后台任务。
- 停止监测后不立即允许重新开始，仍须等待后台发出
  `MonitorEvent('finished', 'stopped')`，由原 `poll` 恢复控件；
  这避免旧会话或工作线程还在使用时重叠启动。
- 删除 `windows_gui.py` 中已无直接用途的旧登录/
  `ScheduledWorkerEvent` 导入，以及事件展示模块的
  `Mapping`、`Sequence` 导入。未改变兼容性函数签名。

## 新增回归测试

`tests/test_windows_session_stop_restart_quality.py` 检查全部
8 类运行标志阻止会话清除；历史接口与身份探测不会与 S202
并发；自动刷新不能与新登录/监测交错；会话过期时撤销内存态并
停止刷新；`RuntimeError` 和 `OSError` 不能把 Worker 留成
僵尸引用；“停止→finished→再次启动”的顺序与按钮状态无回退。

## 明确不作出的声称

这些都是本地 Windows 交互与安全状态的一致性修复。
**CI 不证明**本人真实学校账号登录成功、开放窗口余量有效、
`STU_DynamicInitDataAction.do`／`isSelectableSkbjdm.action`
已核验或正式选课提交可用。未启用自动选课写入。

代码只在通过 Windows 分支测试、源码自检、PyInstaller、打包后
自检和 GUI 冒烟之后合并，合并后再次核验 `main` Artifact。
