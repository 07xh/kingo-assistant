# 第三批清理｜NCWU S202 目录请求与 Windows 只读表格（2026-11-17）

## 本次已审计并提取

上一轮 PR #55 已将 S202 轮次、本人已选门数、已选结果页、开课计划四个查询的
`httpx` 默认配置、`/hsjw` Cookie、共享限流预算及异常分类汇入
`core/ncwu_read_support.py`。此次确认 `ncwu_live_elective.py` 的实时
`tableId=2568` 目录仍重复相同的初始化与 HTTP 401/429 处理。

- 当前 `ncwu_live_elective.py` 改为调用现有的
  `get_optional_verified_budget`、`default_read_client`、
  `set_scoped_cookie` 与 `classify_read_response`。严格保留
  `GET /student/wsxk.zx.html`、`POST getWsxkTimeRange.action?xktype=2`
  及 GBK `POST DataTable.jsp?tableId=2568&fre=1` 顺序、头部与负载。
- 保留 `ActionForm` 唯一性校验、页面字段动态填充、同会话账号/学期/轮次
  一致性、17 列 header-ID 解析、显示班号 `skbh` 与内部
  `skbjdm` 不混用的判断。
- 保留默认超时、禁用系统代理和跨源重定向、同一进程内 60 秒手动查询冷却，
  以及被校方限制时 **429 只施加 Retry-After、不重试** 的行为。
- 原有 `async with _budget_slot` 保持每个请求一个预算槽，不会减少预算统计
  也不会把一组请求合为一次申请。

在 `windows_gui.py`：

- 实时课程目录加入与其余四个只读查询相同的
  `_ncwu_read_busy`、`_start_ncwu_read_worker`、
  `_finish_ncwu_read_event` 路径。异常安全回退、后台线程名及队列
  事件名称不变。
- 保留用户明确授权的自动刷新开关、轮询成功时原有的 **≥60 秒**
  下一次排程与失效会话自动停止；没有启用默认自动刷新。
- `_open_readonly_popup` 统一两个静态只读结果弹窗的标题/尺寸/摘要，
  `_create_readonly_tree` 统一开课计划、S202 结果候选和实时目录的
  `Treeview` 表头及宽度；实际表格内容、滚动条、排序和行插入方式保持原样。
  实时目录仍复用现有的单个弹窗，不切换成每次新建。

## 测试与验收边界

新增模拟 HTTP 测试覆盖可选共享预算、403/429、Retry-After、禁止跨源跳转、
失败响应和 2 MiB 页面上限；模拟 Windows 测试检查实时课程工作线程、
自动刷新开关、原有下一次排程和会话失效停止刷新。静态 UI 测试核对
课程表列 ID、标题、宽度、最低宽度、窗口标题及尺寸。

所有这些测试都是离线回归，不能证明本人教务账号当前已登录成功、学校开放选课
或真实提交。`STU_DynamicInitDataAction.do`、`isSelectableSkbjdm.action`
和选课写接口仍缺完整真实契约，不因本次代码清理而启用。

本轮没有清理使用不同 Cookie 路径的 CAS、学年学期/课表读取模块，
也没有直接删除仍被打包或测试引用的兼容与离线资源。
