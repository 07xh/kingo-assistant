# 真实 HAR 接口接入进度：华水本人课表（2026-11-16）

这项工作直接响应用户反馈：**以前的抓包证据应当驱动 EXE 真正集成接口，而不能只留作离线统计工具**。

## 之前的 HAR 到底确认了什么？

来源：`docs/acceptance/ncwu-xiqueer-har-2026-11-11.md`。在用户自己的 67 条 HAR 请求里，真实观察到：

- `jwmis.ncwu.edu.cn` 上 `POST /hsjw//wap/mycourseschedule.action`，HTTP 200，课表 JSON 包含 `xn`、`xq`、`zc`、`week1..week7` 和 `sjhjinfo`。
- `POST /hsjw//wap/kb_kbdetail_bz.action`，附加数据结构已观察，但无可证明的非空记录。
- 喜鹊儿 `/manager//wap/wapController.jsp` 存在选课相关字段和编码、签名参数，尚不能直接认定为一个可合法复用的选课目录/剩余名额 API。
- 当时没有保存可复用的登录会话、POST 表单字段完整契约，也没有经过学校确认的选课写入/回读协议。HAR 中不应保存或传播 Cookie、动态票据、签名和个人资料。

上述证据已证明真实课表响应接口存在；**它不是可选课程、剩余名额或实际选课成功的证据**。

## 从此前保存的其他脱敏报告补回的接口

另行找到用户 2026-11-03 的华水青果网页 HAR 结构报告（183 条请求）、2026-11-07 喜鹊儿报告（48 条请求）。它们来自不同一次登录与使用流程，不能直接拼成同一可重放的认证会话。

- 已观察到 `POST /hsjw/cas/logon.action`，表单字段名称 `username`、`password`、`loginmethod`；参数值、必要的动态验证和响应身份确认尚未保留。示例配置只预填已观察到的路径与两个账号密码字段名，`auth.login.verified` **仍为 false**。这一校内账号登录表单不是已经实现的跨域 CAS/SSO。
- `GET /hsjw/student/xkjg.wdkb.jsp` 和 `GET /hsjw/student/wsxk.xskcb10319.jsp` 是个人课表 HTML 入口/展示页；它们不等于可选课程目录。
- `GET /hsjw/wsxk/xkjg.ckdgxsxdkchj_data10319.jsp` 是选课结果页面；不能当作当下未选课程或余量。
- `POST /hsjw/jw/common/showYearTerm.action` 返回学年学期信息，并不是可写的选课 POST。

这些源文件在个人文件库的脱敏报告中，不包含原始密钥值、Cookie 或可复用动态提交参数。本轮没有将它们猜测为学校完整协议，也没有合并到选课写入通道。

## 本轮真实代码接入

新模块 `core/native_timetable.py` 使用观察到的**原样双斜杠路径**和 HTTP POST，作为固定、不可由用户任意改成选课提交 URL 的只读适配器：

1. 用户点击首页 **读取本人课表（既有接口 · 只读）**。
2. 检查本机 `known_timetable.verified`、经过人工核对的请求体类型（form/json）和非敏感字段映射，并要求与学校完全一致的 HTTPS 域名。
3. 必须持有 EXE 自己的有效教务会话或显式提供的本人授权会话；遵守已核实的请求频率预算。
4. 在后台线程通过 `httpx` 发起**至多一次** POST，不调用浏览器，不自动重试，不跟随 CAS 跳转。
5. 仅接受预期的 JSON 响应，并用已实现的 `inspect_timetable_json` 核验七天课表及附加项目结构。
6. 只向 UI 返回项目计数、失败类型和明确的“不是选课目录”说明；不输出本人的课表记录、Cookie、票据或原始响应。
7. 若学校要求重新登录、返回登录页或触发限制，则停止并提示。不会由 `HTTP 200` 自动推断登录成功或选课权限。

## 安全的本机配置占位（默认不联网）

```yaml
known_timetable:
  verified: false
  method: POST
  path: "/hsjw//wap/mycourseschedule.action"
  body_mode: ""   # 实际 HAR 证实后设为 form 或 json
  request_fields: {}  # 只允许非敏感的已核验参数
```

设置 `verified: true` 只是软件内部的手动前提，**不是**学校真实授权和正确请求体的独立证明。当前还缺少可用于直接联机验收的完整 POST 参数及软件自己的 CAS 登录会话，所以默认不联网；程序不会猜测字段。

## 2026-11-16 续：复用旧 HAR 的网页课表和已选结果路径

在本人此前上传的 2026-11-03 结构报告中，确认了：
- `GET /hsjw/student/xkjg.wdkb.jsp`：个人课表入口 HTML
- `GET /hsjw/student/wsxk.xskcb10319.jsp`：实际个人课表 HTML，抓包的 `params` 查询值已经脱敏
- `GET /hsjw/wsxk/xkjg.ckdgxsxdkchj_data10319.jsp`：已选课程结果 HTML，实际查询参数是否必需仍待核验
- `POST /hsjw/cas/logon.action`：账号登录表单包含 `username`、`password`、`loginmethod` 三个字段名；`loginmethod` 的有效取值仍未知

新增 `core/known_school_pages.py`，将上述**前两种不同性质的已知网页**（课表入口和已选结果）作为独立单次 GET 只读候选，实现严格的 HTTPS 官方域名与固定路径、已验证查询契约、EXE 内本人会话、共享请求预算、无重试、无自动跳转、有限的 GBK/GB18030 编码支持及 HTML 表头结构识别。结果仅反馈统计计数，**不输出任何姓名、课表或选课数据**；返回 HTML 200 不等于选课成功或身份核验成功。

主界面的“工具与日志”新增 **核验已选课程结果网页（只读）** 按钮，不把该请求混入选课目录或实际自动选课流程。

新增配置：
```yaml
known_school_pages:
  timetable_entry:
    verified: false
    method: GET
    path: "/hsjw/student/xkjg.wdkb.jsp"
    query_verified: false
    query_mode: none
  selected_results:
    verified: false
    method: GET
    path: "/hsjw/wsxk/xkjg.ckdgxsxdkchj_data10319.jsp"
    query_verified: false
    query_mode: unverified
```

这些标志默认关闭。过去 HAR 里的动态 `params` 值无法安全复用；当某入口仍依赖动态查询参数时，这个版本**拒绝构造请求**，而不是复用一次性的签名、票据或猜测参数。只有经独立核验“无需查询参数”时才能启用 `query_mode: none`。

另外，`native_login._contract` 针对已观察到的 `/hsjw/cas/logon.action` 表单会检查非空 `loginmethod`，在缺少经核验取值时提前告知，不会弹出密码输入框后发送不完整登录请求。**此校内表单路径与跨域 CAS/SSO 仍然属于不同登录流程**，不能混用。

## 仍未完成的真实验收

1. 从本人获准读取的真实请求确定课表 POST 的 `Content-Type`、必要的非敏感请求字段，以及会话有效性；不要上传或记录凭证值。
2. 实现并真实测试学校允许的 EXE CAS/教务账号认证流程及身份确认，遵守额外短信/动态口令验证；不绕过任何挑战。
3. 在用户的 Windows EXE 上完成实际一次课表读取，并将请求的真实结果与官方页面核对。
4. 单独核验 *可选课程目录/教学班/剩余名额/开放窗口/官方已选列表*；只有真实证据才可构建选课业务网关。

**自动提交仍未接入**。这一轮的 HTTP POST 是读本人课表的历史接口，不是选课提交。
