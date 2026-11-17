# 2026-11-17 NCWU 接口报告 × KingoAssistant 代码交叉审查

## 已知证据（用户个人会话记录，不是本仓库实时验收）

1. 同一教务站点 `https://jwmis.ncwu.edu.cn/hsjw/` 下存在三个不同的数据源：
   - `POST /hsjw/frame/desk/showLessonScheduleDetail.action`：个人课表 AJAX HTML，`weeks=&xnxq=2026,0`；
   - `POST /hsjw/taglib/DataTable.jsp?tableId=5327042...`：开课计划快照；
   - `POST /hsjw/taglib/DataTable.jsp?tableId=2568&fre=1`：S202 正选目录。
   **绝不能将课表、开课计划与实时余量相互替代。**
2. 报告的 17 列选课表头包含 `head_kcdm`、`head_skbh`、`head_xk_status`、`head_xk_points`，但没有可独立确认实时剩余名额的表头。`xk_points` 只是字段，尚不能证明是竞标规则，更不能擅自推断出价/提交格式。
3. `getWsxkTimeRange.action` 报告的 2026-11-16 响应为未开放；仅 `status:200` 仍需要实际轮次上下文核验，不能把 HTTP 200 等同开放。
4. `STU_ElectCourseAction.do` 无完整经本人选课成功验证的提交体；报告有“无效课程也返回成功”的反例。已选门数 `yxms` 的增量仅是辅助信号；正确回读必须匹配目标课程代码及教学班号，且考虑其他并行操作。
5. `xq`、`xq_m`、`xnxq` 在多个页面分别为 0 基下标、1 基下标或带分隔符的学年学期，不能用一份全局常量转换。
6. 报告内部有两处冲突：`2026,1` 被同时描述为“未定义学年学期”与“第二学期零记录”；晚间时刻既出现官网表格的 19:00/19:55，又保留旧推定的 20:50。须复查原始脱敏响应，不得隐式选择其一为全局事实。
7. 单次会话中 `xh` 参数无法切换身份、Cookie 可跨设备重放等实验，只能作为该次会话观察，不足以推导全站永久不绑 IP。把 `JSESSIONID` 视为密码级秘密，不存仓库、不写日志、不读浏览器进程内存。
8. “空教室学生端不存在”“成绩总是受评教限制”“几乎全年可调用”等是有限时间和路径观察，不是长期或全局结论。

## 当前仓库对照

- `core/ncwu_signed_login.py` 已实现受约束的本人登录入口，挑战或锁定必须停止；未复用报告的 QR 令牌或 Cookie 内存扫描脚本。
- `core/ncwu_elective_table.py` 和 `core/ncwu_live_elective.py` 已区分表头证据、课程目录与实时余量；旧的 2568 只读路径保留。
- `core/ncwu_course_plan.py` 已保留独立的 5327042 计划快照。
- `core/ncwu_s202_readback.py` 已支持课程/教学班回读候选，但仍需本人选课结果核对。
- 本分支新增 `core/ncwu_lesson_detail.py`：使用 EXE 本人会话对已知课表 AJAX 路径执行一次无重试 POST，严格核对 12 节次和学期标题，仅返回未经独立核验的课表单元格；独立于旧 WAP JSON 和 S202 目录，不启用自动选课。

## 明确的未完成项

- 未验证本分支对华水服务器的真实请求，也未将新课表 AJAX 按钮接入 Windows GUI；必须在本人 EXE 中完成实际回读后再暴露为默认入口。
- 尚无可以直接写入自动选课调度器的完整已授权提交契约、余量字段与目标班级成功凭证。
- 本分支测试使用合成 HTML/HTTP；它们证明的是本地程序行为，不证明学校端当前可用。
- 报告中的 `session.json`、二维码令牌、Cookie 等不得上传到公有仓库；禁止由程序自动收集其他设备的浏览器凭据。
