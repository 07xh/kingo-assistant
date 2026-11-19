# KingoAssistant — Windows 桌面客户端

**唯一维护中的 Windows 源码目录：** [`source/`](source/)

此仓库已在 `clients/windows/source/` 中直接跟踪完整源码与测试，**不再依赖 V4.6 历史压缩包解压或自动提交源码**。历史 V4.6 版本仍可在 Git 提交历史中查阅，但不应重新覆盖当前的登录、S202 查询或志愿校验功能。

## 获取最新 EXE

打开仓库根目录的 [Windows standalone EXE 工作流](https://github.com/07xh/kingo-assistant/actions/workflows/windows-build.yml)，选择对应 **main** 提交的成功构建，在 **Artifacts** 中下载 `KingoAssistant-Windows-portable`。这是一份 Actions 构建工件，并非自动发布到 Releases 的安装包。

此工作流使用 **仓库根目录** `.github/workflows/windows-build.yml`，负责源码测试、PyInstaller、打包后自检和 Tk GUI 冒烟测试；不使用 `source/.github/workflows/` 下的历史副本。

## 从当前源码构建

在 Windows 10/11 电脑中：

1. 安装 Python 3.10 或更高版本。
2. 在 `clients/windows/source/` 运行 `build-windows.bat`。
3. 构建成功后，检查 `dist/KingoAssistant-Windows.exe`、`dist/SHA256.txt` 与 `dist/build-verification.json`。失败时构建脚本会移除过期产物，避免误用旧 EXE。

仅需运行源码 GUI 可使用 `run-windows.bat`，发生启动异常时可使用 `debug-windows.bat`。详细用法见 [源码说明](source/README.md)。

## 当前功能边界

**统一目标：[纯 HTTP 登录 + 纯 HTTP 提交 + 官方回读](../../docs/architecture/pure-api-execution.md)。** 浏览器登录、手动官网预约和混合执行不再纳入正式方案；旧版 UI 若仍出现，属于待删除的历史实现，不能误称代码已清理。


已实现 EXE 自身登录会话、S202 轮次与课程目录只读查询、开课计划快照、已选结果和门数读取、课程与志愿候选校验、预约时刻及本地监测、离线 HAR 字段诊断。**这些源码和模拟测试的通过不等同于本人账号的真实学校接口验收。**

`STU_DynamicInitDataAction.do`、`isSelectableSkbjdm.action` 以及正式选课提交的有效协议尚待完整核验；不要将当前版本标为已经能够自动选课。
