# 选课目录和剩余名额：只读验收规程（2026-11-11）

## 当前结论

**真实选课目录读取：未验收。剩余名额字段：未验收。** 已抓到的 `mycourseschedule.action` 是个人课表 JSON，不是选课目录接口。喜鹊儿 `zxinfo.zxxq` 等记录也不能被解释为开放选课目录；`yixuan`、`kex`、`kcid`、`skbj`、`kcmc` 在缺乏官方页面对照时不得用作余量字段。

学校官网《2026—2027学年第二学期教学工作安排》提及 **2026-12-21 至 2026-12-25** 计划进行公选课/体育选课，以及选课前数据处理。这不是指定到小时、年级和校区的正式开放公告。最终仍以面向学生的后续官方通知、本人账户实际可见权限为准。

来源：<https://ncwu.edu.cn/info/1063/23332.htm>

## 验收前提

1. **合法开放窗口**：学校已对本人年级、校区和选课类别正式开放，官方页面直接能显示课程目录，而不是报“当前时间不是有效的选课时间区段”。
2. **本人授权的只读证据**：仅检查自己正常登录后页面上可见的列表和与之对应的合法只读响应；不绕过验证码、限流、访问控制或调用隐藏写入接口。
3. **课程身份对应**：必须明确区分课程和教学班，至少核对两个不同教学班，并能在官方 UI 与只读数据中一一对应。
4. **余量字段语义**：显示“已选人数”“可选状态”或课程容量，不代表“剩余名额”；应当核对官方 UI 中真实显示的数字。若没有同时看到零余量和正余量的样本，不直接认定字段覆盖完整。
5. **时效与异常**：对照的两个观察值应尽量在 90 秒内取得；出现时间差、结构不匹配、登录失效、403/429 或显示不同步时，标记为未知并停止。
6. **人工最终复核**：离线工具只证明两份经过本地脱敏处理的样例*结构一致*，不能证明接口受官方支持、字段含义正确、页面真实开放或选课成功。

## 离线验收器

`clients/windows/source/tools/catalog_evidence.py` 不访问网络；输入必须是 **本地脱敏的 JSON 对照表**，不是原始 HAR。只比较匿名编号和余量整数，输出汇总、不输出课程信息；文档若含 Cookie、Token、签名等敏感键会被拒绝。

用法（Python 环境）：

```powershell
python clients/windows/source/tools/catalog_evidence.py C:\\private\\catalog-evidence.json
```

匿名演示模板（**全是虚构数据，绝不代表真实接口字段**）：

```json
{
  "version": 1,
  "source": "official_readonly_catalog",
  "official_selection_window_open": true,
  "official_ui_checked_by_user": true,
  "candidate_remaining_field": "remaining_candidate",
  "ui": {
    "observed_at": "2026-12-23T09:00:00+08:00",
    "rows": [
      {"key": "COURSE_A", "remaining": 0},
      {"key": "COURSE_B", "remaining": 3}
    ]
  },
  "response": {
    "observed_at": "2026-12-23T09:00:30+08:00",
    "rows": [
      {"key": "COURSE_A", "remaining": 0},
      {"key": "COURSE_B", "remaining": 3}
    ]
  }
}
```

输出只有三种状态：

- `blocked`：存在缺失、歧义、不安全数据或数值矛盾。
- `needs_more_evidence`：匹配样本不足以覆盖零余量与正余量。
- `ready_for_manual_review`：供**人工复核**，仍不代表真实接口已验收。

**不得**使用工具的结构比对结果自动将 `protocol.verified` 改为 `true`，也不得把 `mycourseschedule.action` 伪装成选课目录接口。只在获得官方页面的真实目录字段对照、人工检查通过且访问方式合规时再设计相应解析器。

## 隐私与代码发布

完整 HAR、课程截图、真实教学班标识、会话信息和本地对照 JSON **不进入公开 GitHub**。应用测试只用合成数据。当前仓库及 EXE 保留只读、失败即停止的安全边界。
