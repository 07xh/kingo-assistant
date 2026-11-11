# Windows V4.6 — Native-build verification gates and offline HAR audit

## 代码变更

- 新增打包后 `--smoke-gui`：在**隔离临时目录**初始化真实 Tk 窗口，检查内置虚构样例和恢复默认课程配置；不调用学校接口。
- 新增 `tools/verify_windows_build.py`：必须在原生 Windows 运行，验证 PE 文件头、打包应用 `--self-test` 和 `--smoke-gui`；每个检查均设置超时，失败即清理旧的成功标记。
- 更新 `build-windows.bat` 与 GitHub Actions：统一调用上述验证器，通过后产生 `SHA256.txt` 和 `build-verification.json`。
- 新增「离线工具→本机 HAR 结构检查」：只统计已合法获得的华水 HTTPS HAR 请求方法、HTTP 状态、响应类型；不导出请求值、URL、Cookie、正文，不重放请求，也不能判定 POST 为只读。
- 新增 6 项测试覆盖 Windows 验证器的失败分支、秘密字段不泄漏、HAR 结构错误；总计 72 项离线单元测试。源码 `--self-test` 与 Xvfb 的 `--smoke-gui` 已通过。

## 验收边界

- **尚未获得** Windows 原生构建的 EXE；Windows GUI 和打包后启动必须由 Windows 构建机实际运行验证器后才算通过。
- 华水教务域名由公开学校公告确认，**学校实际课程列表与剩余名额的接口没有核实**，不能按模拟结果宣称真实读取成功。
- 不提供自动选课提交、验证码破解、高频并发、限流绕过。
- 仅源码包；未上传 GitHub，也未修改校序仓库。
