# 华水 EXE 内登录 → 学期接口账号一致性核验（2026-11-16）

## 目标

将已观测的、无需请求体的 `POST /hsjw/jw/common/showYearTerm.action` 与现有 EXE 原生登录串接，而不是依赖虚构的个人身份接口或旧 HAR 的动态签名。

原始授权 HAR（11 月 3 日、11 月 14 日）表明该接口在浏览器已登录时返回 `userCode`、`jwUserYeartermId`、`xn`、`xqM`、`xqName` 字段。但 **仅从已登录样本不能推断它对未登录请求一定拒绝**。因此这一版本必须先有独立的 `known_year_term.identity_binding_verified` 证据，才允许把字段值当作登录身份证明。

## 本轮已完成的软件链路

1. 使用原有 **软件内登录并核对账号** 按钮。登录模块在**输入密码前**逐项核对：
   - `auth.login.mode: direct_form`，官方同源 HTTPS 路径、已核验请求字段/表单模式、合法访问范围。
   - `auth.login.contract_variant: plain_20261103`（只是抓包中某个旧变体，必须确认当前仍合法适用）；`signed_20261114` 不支持静态旧令牌重放。
   - `auth.login.identity_mode: year_term` 且 `known_year_term.verified: true` 和 `known_year_term.identity_binding_verified: true`；至少要确认该接口确实需要登录，返回 `userCode` 是当前已认证账号的标识。
   - 已核验的官方请求策略；不能伪造身份接口或自行放宽频率限制。
2. 提交一次经过独立验证的账号登录表单；遇到验证码、动态口令、跨域 CAS 等额外认证，按学校实际方式处理，当前适配器不能绕过。
3. 只在已获得同源会话 Cookie 后，发起一次已核验的空体学期 POST；从响应中读取字段并在**内存**用恒定时间比较本次登录账号与 `userCode`，不返回或保存学号/身份标识。
4. 只有两者完全匹配，才返回 `NativeLoginResult(verified)` 并保留 EXE 内存 Cookie。错误、缺失、429、未核验、身份不匹配均清空；不把 HTTP 200 或课表结构当作身份成功。
5. 登录后已有的 **核验学期接口（已抓包·只读）** 与 **读取本人课表（既有接口·只读）** 仍保持各自独立的协议门禁；登录通过 **不启用** 选课余量或提交。

## 示例配置（全部默认关闭）

```yaml
auth:
  login:
    mode: direct_form
    contract_variant: ""         # plain_20261103 / signed_20261114, 后者暂不支持
    verified: false
    identity_mode: year_term
    method: POST
    path: "/hsjw/cas/logon.action"
    username_field: username
    password_field: password
    static_fields: {}            # 不能猜旧 loginmethod
known_year_term:
  verified: false
  identity_binding_verified: false
  method: POST
  path: "/hsjw/jw/common/showYearTerm.action"
  request_mode: empty
```

这些标记只用于软件工程约束，不能自行设为 true 就宣称学校官方授权已得到。

## 下一步必须由真实环境验收的事项

- 核实学校目前允许的登录方式：同一地址在 11 月 3 日与 11 月 14 日抓包的请求结构不同；如当前使用动态签名、短信、验证码或 CAS，先取得正式支持的授权接入办法。
- 核实 `showYearTerm.action` 的 **匿名请求 vs 本人已登录请求** 的正常访问权限差异，以及 `userCode` 是否确实等于登录账号。须遵守学校规则，不做未经许可的重放、爆破、频率压力测试。
- 用经核验的真实账号在 EXE 内成功完成这条链路，并在学校官网独立核对身份；不得把 CI 离线 MockTransport 结果当作实测。
- 后续分别核验动态课表 `param/param2` 的合法生成方式、选课目录/教学班/剩余名额/实际窗口；**真实自动提交仍禁用**。

## CI 验收范围

测试使用完全合成的 `userCode` 和 Cookie，覆盖正反账号匹配、空值、身份协议缺失、签名版拒绝、HTTP 响应和 no-network 安全门禁。Windows EXE 可打包通过仅表示模块工作流及 UI 符合既定测试，不证明当前学校登录已经可用。
